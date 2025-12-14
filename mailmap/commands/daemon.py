"""Daemon mode - IMAP listener and email processor."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..categories import get_category_descriptions, load_categories
from ..config import Config
from ..database import Database, Email
from ..imap_client import EmailMessage, ImapListener
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

    processor_task = asyncio.create_task(processor.process_loop())

    listener = ImapListener(config.imap)

    def on_new_email(message: EmailMessage) -> None:
        logger.info(f"New email in {message.folder}: {message.subject[:50]}...")
        processor.enqueue(message)

    try:
        await listener.start(on_new_email)
    finally:
        listener.stop()
        processor_task.cancel()


async def run_daemon(config: Config, db: Database) -> None:
    """Run the full mailmap daemon."""
    from ..websocket_server import run_websocket_server

    db.connect()
    db.init_schema()

    try:
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
