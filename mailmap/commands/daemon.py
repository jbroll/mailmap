"""Daemon mode - IMAP listener and email processor."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..categories import get_category_descriptions, load_categories
from ..config import Config
from ..database import Database, Email
from ..imap_client import EmailMessage, ImapListener, ImapMailbox
from ..llm import OllamaClient

logger = logging.getLogger("mailmap")


class EmailProcessor:
    """Process incoming emails through the classification pipeline."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._queue: asyncio.Queue[EmailMessage] = asyncio.Queue()

    def enqueue(self, message: EmailMessage) -> None:
        """Add a message to the processing queue."""
        self._queue.put_nowait(message)

    async def process_loop(self) -> None:
        """Main processing loop for incoming emails."""
        while True:
            message = await self._queue.get()
            try:
                await self._process_email(message)
            except Exception as e:
                logger.error(f"Error processing email {message.message_id}: {e}")
            finally:
                self._queue.task_done()

    async def _process_email(self, message: EmailMessage) -> None:
        """Process a single email through classification."""
        logger.info(f"Processing email: {message.subject[:50]}...")

        email_record = Email(
            message_id=message.message_id,
            folder_id=message.folder,
            subject=message.subject,
            from_addr=message.from_addr,
            mbox_path="",  # IMAP emails don't have mbox_path
            processed_at=datetime.now(),
        )
        self.db.insert_email(email_record)

        categories = load_categories(self.config.database.categories_file)
        folder_descriptions = get_category_descriptions(categories)
        if not folder_descriptions:
            logger.warning("No categories available, skipping classification")
            return

        async with OllamaClient(self.config.ollama) as llm:
            classification = await llm.classify_email(
                message.subject,
                message.from_addr,
                message.body_text,
                folder_descriptions,
            )

        self.db.update_classification(
            message.message_id, classification.predicted_folder, classification.confidence
        )
        logger.info(
            f"Classified as '{classification.predicted_folder}' (confidence: {classification.confidence:.2f})"
        )


async def run_listener(config: Config, db: Database) -> None:
    """Run the IMAP listener and email processor."""
    processor = EmailProcessor(config, db)
    loop = asyncio.get_event_loop()

    processor_task = asyncio.create_task(processor.process_loop())

    listener = ImapListener(config.imap)

    def on_new_email(message: EmailMessage) -> None:
        """Callback from IMAP thread - must use thread-safe scheduling."""
        logger.info(f"New email in {message.folder}: {message.subject[:50]}...")
        # Schedule enqueue on the event loop (called from thread)
        loop.call_soon_threadsafe(processor.enqueue, message)

    try:
        await listener.start(on_new_email)
    finally:
        listener.stop()
        processor_task.cancel()


async def process_existing_emails(config: Config, db: Database) -> int:
    """Process existing unclassified emails in monitored folders.

    Returns the number of emails processed.
    """
    mailbox = ImapMailbox(config.imap)
    processor = EmailProcessor(config, db)
    processed = 0

    try:
        mailbox.connect()
        logger.info("Checking for existing unclassified emails...")

        for folder in config.imap.idle_folders:
            uids = mailbox.fetch_recent_uids(folder, limit=100)
            logger.info(f"Found {len(uids)} recent emails in {folder}")

            for uid in uids:
                msg = mailbox.fetch_email(uid, folder)
                if msg:
                    # Check if already classified
                    existing = db.get_email(msg.message_id)
                    if existing and existing.classification:
                        continue  # Already classified

                    # Process directly (not queued)
                    try:
                        await processor._process_email(msg)
                        processed += 1
                    except Exception as e:
                        logger.error(f"Error processing {msg.message_id}: {e}")

        logger.info(f"Processed {processed} existing emails")
    finally:
        mailbox.disconnect()

    return processed


async def run_daemon(config: Config, db: Database, *, process_existing: bool = False) -> None:
    """Run the full mailmap daemon."""
    from ..websocket_server import run_websocket_server

    db.connect()
    db.init_schema()

    try:
        # Process existing emails if requested (before starting listener)
        if process_existing:
            await process_existing_emails(config, db)

        # Build list of services to run
        tasks = []

        # IMAP listener
        logger.info("Starting email listener...")
        tasks.append(run_listener(config, db))

        # WebSocket server (if enabled)
        if config.websocket.enabled:
            logger.info("Starting WebSocket server...")
            tasks.append(run_websocket_server(config.websocket, db, config.database.categories_file))

        # Run all services concurrently
        await asyncio.gather(*tasks)
    finally:
        db.close()
