"""Main entry point and orchestration for mailmap."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .categories import Category, get_category_descriptions, load_categories, save_categories
from .config import Config, load_config
from .database import Database, Email
from .imap_client import EmailMessage, ImapListener, ImapMailbox
from .llm import OllamaClient, SuggestedFolder
from .spam import is_spam, parse_rules
from .thunderbird import ThunderbirdReader

if TYPE_CHECKING:
    from .websocket_server import WebSocketServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mailmap")

# System folders to exclude when learning from user's folder structure
SYSTEM_FOLDERS = {
    "INBOX",
    "Inbox",
    "Sent",
    "Sent Items",
    "Sent Mail",
    "Drafts",
    "Draft",
    "Trash",
    "Deleted Items",
    "Deleted",
    "Junk",
    "Junk E-mail",
    "Spam",
    "Archive",
    "Archives",
    "All Mail",
    "Outbox",
    "Notes",
    "Calendar",
    "Contacts",
    "Tasks",
}


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
    from .websocket_server import run_websocket_server

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


def is_system_folder(folder_name: str) -> bool:
    """Check if a folder is a system folder that should be excluded."""
    # Check exact match
    if folder_name in SYSTEM_FOLDERS:
        return True
    # Check if any part of a hierarchical folder matches (e.g., "INBOX/subfolder")
    parts = folder_name.replace("\\", "/").split("/")
    return any(part in SYSTEM_FOLDERS for part in parts)


async def learn_from_existing_folders(config: Config) -> None:
    """Learn classification categories from user's existing folder structure.

    This scans all non-system folders, samples emails from each, generates
    descriptions, and saves categories to categories.txt.
    """
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Learning from existing folder structure...")
    try:
        reader = ThunderbirdReader(profile_path)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # Get all folders (qualified) and extract unique folder names
    all_qualified = reader.list_folders_qualified()
    # Extract unique folder names (without server prefix)
    seen_names = set()
    user_folders = []
    for qualified in all_qualified:
        _, folder_name = qualified.split(":", 1) if ":" in qualified else (None, qualified)
        if folder_name not in seen_names and not is_system_folder(folder_name):
            seen_names.add(folder_name)
            user_folders.append((qualified, folder_name))

    system_folders = [f.split(":", 1)[1] if ":" in f else f for f in all_qualified
                      if is_system_folder(f.split(":", 1)[1] if ":" in f else f)]
    system_folders = list(set(system_folders))

    logger.info(f"Found {len(all_qualified)} total folders")
    logger.info(f"System folders (excluded): {system_folders}")
    logger.info(f"User folders to learn from: {[name for _, name in user_folders]}")

    if not user_folders:
        logger.warning("No user folders found to learn from")
        return

    # Load existing categories to merge with
    categories_path = Path(config.database.categories_file)
    existing_categories = load_categories(categories_path)
    existing_names = {cat.name for cat in existing_categories}
    new_categories = []

    for folder_spec, folder_name in user_folders:
        # Skip if category already exists
        if folder_name in existing_names:
            logger.info(f"Category '{folder_name}' already exists, skipping")
            continue

        logger.info(f"Processing folder: {folder_name}")

        # Sample emails from this folder for description generation
        samples = reader.get_sample_emails(
            folder_spec,
            count=tb_config.samples_per_folder,
        )

        if not samples:
            logger.info(f"  No emails in {folder_name}, skipping")
            continue

        # Generate folder description from samples
        sample_dicts = [
            {
                "subject": s.subject,
                "from_addr": s.from_addr,
                "body": s.body_text[:500],
            }
            for s in samples
        ]

        async with OllamaClient(config.ollama) as llm:
            result = await llm.generate_folder_description(folder_name, sample_dicts)

        new_categories.append(Category(name=folder_name, description=result.description))
        logger.info(f"  Created category '{folder_name}': {result.description[:60]}...")

    # Merge and save categories
    all_categories = existing_categories + new_categories
    save_categories(all_categories, categories_path)

    logger.info(f"Learning complete: {len(new_categories)} new categories added")
    logger.info(f"Total categories: {len(all_categories)} (saved to {categories_path})")


async def run_learn_folders(config: Config) -> None:
    """Run learn-folders mode."""
    await learn_from_existing_folders(config)


async def bulk_classify_from_thunderbird(
    config: Config,
    db: Database,
    ws_server: WebSocketServer | None = None,
    move: bool = False,
    target_account: str = "local",
    min_confidence: float = 0.5,
) -> list[tuple[str, str]]:
    """Bulk classify emails from Thunderbird using existing categories.

    Reads emails from Thunderbird profile and classifies them using
    the categories defined in categories.txt.

    Args:
        config: Application configuration
        db: Database instance
        ws_server: Optional WebSocket server for immediate copy/move after classification
        min_confidence: Minimum confidence to copy/move (below this goes to Unknown)
        move: If True with ws_server, move messages; otherwise copy
        target_account: Target account for folders when using ws_server

    Returns:
        List of (message_id, classification) tuples for successfully classified emails.
    """
    from .protocol import Action

    classifications: list[tuple[str, str]] = []
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    # Load categories
    categories_path = Path(config.database.categories_file)
    categories = load_categories(categories_path)
    folder_descriptions = get_category_descriptions(categories)

    if not folder_descriptions:
        logger.error(f"No categories found in {categories_path}")
        logger.error("Run 'mailmap learn' first to generate categories")
        return classifications

    logger.info(f"Loaded {len(folder_descriptions)} categories from {categories_path}")

    # Initialize Thunderbird reader
    logger.info("Initializing Thunderbird reader...")
    try:
        reader = ThunderbirdReader(profile_path)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return classifications

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # Get folders to process
    if tb_config.folder_filter:
        # Validate folder upfront (will error if ambiguous)
        try:
            server, folder_name = reader.resolve_folder(tb_config.folder_filter)
            # Use server:folder format for unambiguous access
            folders = [f"{server}:{folder_name}"]
            logger.info(f"Processing folder: {folder_name} (from {server})")
        except ValueError as e:
            logger.error(str(e))
            return classifications
    else:
        # Get all folders with server prefix to avoid ambiguity
        folders = reader.list_folders_qualified()

    # Load spam rules
    spam_rules = parse_rules(config.spam.rules) if config.spam.enabled else []

    total_imported = 0
    total_classified = 0
    total_copied = 0
    total_failed = 0
    total_spam = 0

    action = Action.MOVE_MESSAGES if move else Action.COPY_MESSAGES
    action_verb = "Moving" if move else "Copying"
    action_past = "moved" if move else "copied"

    for folder_spec in folders:
        # Extract folder name for display and skip checks
        if ":" in folder_spec:
            _, folder_name = folder_spec.split(":", 1)
        else:
            folder_name = folder_spec

        # Skip spam folders
        if config.spam.enabled and folder_name in config.spam.skip_folders:
            logger.info(f"Skipping spam folder: {folder_spec}")
            continue

        logger.info(f"Processing folder: {folder_spec}")
        folder_count = 0

        # Choose between random sampling and sequential reading
        limit = int(tb_config.import_limit) if isinstance(tb_config.import_limit, (int, float)) else None
        if tb_config.random_sample and limit:
            email_iterator = reader.read_folder_random(folder_spec, limit)
        else:
            email_iterator = reader.read_folder(folder_spec, limit=limit)

        for tb_email in email_iterator:
            # Check if already processed
            existing = db.get_email(tb_email.message_id)
            if existing:
                continue

            # Check for spam
            headers = tb_email.headers or {}
            is_spam_result, spam_reason = is_spam(headers, spam_rules) if spam_rules else (False, None)
            if is_spam_result:
                email_record = Email(
                    message_id=tb_email.message_id,
                    folder_id=folder_name,
                    subject=tb_email.subject,
                    from_addr=tb_email.from_addr,
                    mbox_path=tb_email.mbox_path,
                    is_spam=True,
                    spam_reason=spam_reason,
                    processed_at=datetime.now(),
                )
                db.insert_email(email_record)
                total_spam += 1
                continue

            # Import email
            email_record = Email(
                message_id=tb_email.message_id,
                folder_id=folder_name,
                subject=tb_email.subject,
                from_addr=tb_email.from_addr,
                mbox_path=tb_email.mbox_path,
                processed_at=datetime.now(),
            )
            db.insert_email(email_record)
            total_imported += 1
            folder_count += 1

            # Classify email
            try:
                async with OllamaClient(config.ollama) as llm:
                    result = await llm.classify_email(
                        tb_email.subject,
                        tb_email.from_addr,
                        tb_email.body_text,
                        folder_descriptions,
                    )
                db.update_classification(
                    tb_email.message_id,
                    result.predicted_folder,
                    result.confidence,
                )
                classifications.append((tb_email.message_id, result.predicted_folder))
                total_classified += 1

                # Immediately copy/move if WebSocket server is connected
                if ws_server and ws_server.is_connected:
                    # Use predicted folder if confidence is high enough, otherwise Unknown
                    target_folder = result.predicted_folder if result.confidence >= min_confidence else "Unknown"
                    target_folder_spec = {"path": target_folder, "accountId": target_account}
                    response = await ws_server.send_request(
                        action,
                        {
                            "headerMessageIds": [tb_email.message_id],
                            "targetFolder": target_folder_spec,
                        },
                        timeout=30.0,
                    )
                    if response and response.ok:
                        result_data = response.result or {}
                        success_count = result_data.get("moved" if move else "copied", 0)
                        total_copied += success_count
                        if success_count:
                            conf_str = f" ({result.confidence:.0%})" if target_folder != "Unknown" else f" (low: {result.confidence:.0%})"
                            logger.info(f"  {action_past}: {tb_email.subject[:40]}... -> {target_folder}{conf_str}")
                        else:
                            not_found = result_data.get("notFound", [])
                            if not_found:
                                logger.warning(f"  Not found in Thunderbird: {tb_email.message_id}")
                                total_failed += 1
                    else:
                        error = response.error if response else "No response"
                        logger.error(f"  Failed to {action_verb.lower()}: {error}")
                        total_failed += 1

            except Exception as e:
                logger.warning(f"Failed to classify {tb_email.message_id}: {e}")

        logger.info(f"  Processed {folder_count} emails from {folder_name}")

    logger.info(f"Classification complete: {total_imported} imported, {total_classified} classified, {total_spam} spam")
    if ws_server:
        logger.info(f"Extension actions: {total_copied} {action_past}, {total_failed} failed")
    return classifications


async def send_to_extension(
    config: Config,
    classifications: list[tuple[str, str]],
    move: bool = False,
    target_account: str = "local",
) -> None:
    """Send copy/move commands to Thunderbird extension via WebSocket.

    Args:
        config: Application configuration
        classifications: List of (message_id, target_folder) tuples
        move: If True, move messages; otherwise copy
        target_account: Target account for folders: 'local', 'imap', or account ID
    """
    from collections import defaultdict

    from .protocol import Action
    from .websocket_server import WebSocketServer

    # Group by target folder
    by_folder: dict[str, list[str]] = defaultdict(list)
    for message_id, folder in classifications:
        by_folder[folder].append(message_id)

    logger.info(f"Preparing to {'move' if move else 'copy'} {len(classifications)} emails to {len(by_folder)} folders")
    logger.info(f"Target account: {target_account}")

    # Start WebSocket server
    ws_config = config.websocket
    if not ws_config.enabled:
        logger.error("WebSocket is not enabled in config. Add [websocket] section with enabled=true")
        return

    # Create a temporary database connection for the server (it needs one for queries)
    from .database import Database
    temp_db = Database(config.database.path)
    temp_db.connect()

    try:
        server = WebSocketServer(ws_config, temp_db, config.database.categories_file)

        # Start server in background
        server_task = asyncio.create_task(server.start())

        logger.info(f"WebSocket server started on ws://{ws_config.host}:{ws_config.port}")
        logger.info("Waiting for Thunderbird extension to connect...")

        # Wait for extension to connect (timeout after 60 seconds)
        for _ in range(60):
            if server.is_connected:
                break
            await asyncio.sleep(1)
        else:
            logger.error("Timeout waiting for extension to connect")
            await server.stop()
            server_task.cancel()
            return

        logger.info("Extension connected!")

        # Send copy/move commands for each folder
        action = Action.MOVE_MESSAGES if move else Action.COPY_MESSAGES
        total_success = 0
        total_failed = 0

        for folder, message_ids in by_folder.items():
            logger.info(f"  {'Moving' if move else 'Copying'} {len(message_ids)} emails to {folder}...")

            # Build target folder spec with account
            target_folder_spec: dict[str, str] = {"path": folder}
            if target_account:
                target_folder_spec["accountId"] = target_account

            response = await server.send_request(
                action,
                {
                    "headerMessageIds": message_ids,
                    "targetFolder": target_folder_spec,
                },
                timeout=60.0,
            )

            if response and response.ok:
                result = response.result or {}
                success_count = result.get("moved" if move else "copied", 0)
                not_found = result.get("notFound", [])
                total_success += success_count
                total_failed += len(not_found)
                if not_found:
                    logger.warning(f"    {len(not_found)} messages not found in Thunderbird")
            else:
                error = response.error if response else "No response"
                logger.error(f"    Failed: {error}")
                total_failed += len(message_ids)

        logger.info(f"Complete: {total_success} {'moved' if move else 'copied'}, {total_failed} failed")

        # Stop server
        await server.stop()
        server_task.cancel()

    finally:
        temp_db.close()


async def run_bulk_classify(
    config: Config,
    db: Database,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
) -> None:
    """Run bulk classification mode.

    Args:
        config: Application configuration
        db: Database instance
        copy: If True, copy classified emails to target folders via extension
        move: If True, move classified emails to target folders via extension
        target_account: Target account for folders: 'local', 'imap', or account ID
    """
    from .websocket_server import WebSocketServer

    if copy and move:
        logger.error("Cannot specify both --copy and --move")
        return

    db.connect()
    db.init_schema()

    ws_server = None
    server_task = None

    try:
        # If copy or move requested, start WebSocket server and wait for extension
        if copy or move:
            ws_config = config.websocket
            if not ws_config.enabled:
                logger.error("WebSocket is not enabled in config. Add [websocket] section with enabled=true")
                return

            ws_server = WebSocketServer(ws_config, db, config.database.categories_file)
            server_task = asyncio.create_task(ws_server.start())

            logger.info(f"WebSocket server started on ws://{ws_config.host}:{ws_config.port}")
            logger.info("Waiting for Thunderbird extension to connect...")

            # Wait for extension to connect (timeout after 60 seconds)
            for _ in range(60):
                if ws_server.is_connected:
                    break
                await asyncio.sleep(1)
            else:
                logger.error("Timeout waiting for extension to connect")
                await ws_server.stop()
                server_task.cancel()
                return

            logger.info("Extension connected! Starting classification with immediate copy/move...")

        # Classify emails (with immediate copy/move if ws_server is provided)
        await bulk_classify_from_thunderbird(
            config, db, ws_server=ws_server, move=move, target_account=target_account
        )

    finally:
        # Stop WebSocket server if it was started
        if ws_server:
            await ws_server.stop()
        if server_task:
            server_task.cancel()
        db.close()


def list_classifications(db: Database, limit: int = 50) -> None:
    """List classification results from the database."""
    db.connect()
    db.init_schema()
    try:
        rows = db.conn.execute(
            """
            SELECT message_id, folder_id, subject, from_addr, classification, confidence, processed_at
            FROM emails
            WHERE classification IS NOT NULL
            ORDER BY processed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            print("No classification results found.")
            return

        print(f"{'Subject':<40} {'From':<25} {'Original':<15} {'Predicted':<15} {'Conf':<6}")
        print("-" * 105)

        for row in rows:
            subject = (row["subject"] or "")[:38]
            from_addr = (row["from_addr"] or "")[:23]
            folder = (row["folder_id"] or "")[:13]
            classification = (row["classification"] or "")[:13]
            confidence = row["confidence"] or 0

            print(f"{subject:<40} {from_addr:<25} {folder:<15} {classification:<15} {confidence:.2f}")

        print(f"\nTotal: {len(rows)} results (showing up to {limit})")
    finally:
        db.close()


def list_categories_cmd(config: Config) -> None:
    """List categories from categories file."""
    categories_path = Path(config.database.categories_file)
    categories = load_categories(categories_path)

    if not categories:
        print(f"No categories found in {categories_path}")
        print("Create a categories.txt file or run 'mailmap init' to generate categories.")
        return

    print(f"{'Category':<25} {'Description':<75}")
    print("-" * 102)

    for cat in categories:
        name = cat.name[:23]
        desc = cat.description[:73]
        print(f"{name:<25} {desc:<75}")

    print(f"\nTotal: {len(categories)} categories (from {categories_path})")


def clear_cmd(db: Database, folder: str | None = None) -> None:
    """Clear classifications from emails."""
    db.connect()
    db.init_schema()
    try:
        count = db.clear_classifications(folder)
        if folder:
            print(f"Cleared classifications from {count} emails in folder '{folder}'")
        else:
            print(f"Cleared classifications from {count} emails")
    finally:
        db.close()


def summary_cmd(db: Database) -> None:
    """Show classification summary with counts per category."""
    db.connect()
    db.init_schema()
    try:
        total = db.get_total_count()
        classified = db.get_classified_count()
        spam = db.get_spam_count()
        unclassified = total - classified - spam

        # Get counts per classification (excluding spam)
        rows = db.conn.execute(
            """
            SELECT classification, COUNT(*) as count
            FROM emails
            WHERE classification IS NOT NULL AND is_spam = 0
            GROUP BY classification
            ORDER BY count DESC
            """
        ).fetchall()

        if not rows and spam == 0:
            print("No classified emails found.")
            return

        print(f"{'Category':<35} {'Count':>8} {'Percent':>8}")
        print("-" * 53)

        for row in rows:
            category = (row["classification"] or "")[:33]
            count = row["count"]
            pct = 100 * count / total if total > 0 else 0
            print(f"{category:<35} {count:>8} {pct:>7.1f}%")

        print("-" * 53)
        print(f"{'Classified':<35} {classified:>8} {100*classified/total if total else 0:>7.1f}%")
        if spam > 0:
            print(f"{'Spam (skipped)':<35} {spam:>8} {100*spam/total if total else 0:>7.1f}%")
        if unclassified > 0:
            print(f"{'Unclassified':<35} {unclassified:>8} {100*unclassified/total if total else 0:>7.1f}%")
        print(f"{'Total':<35} {total:>8}")
    finally:
        db.close()


async def init_folders_from_samples(config: Config) -> None:
    """Analyze sample emails iteratively in batches to build folder structure."""
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Initializing folder structure from email samples (iterative batching)...")

    try:
        reader = ThunderbirdReader(profile_path)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Reading emails from Thunderbird profile: {reader.profile_path}")

    # Determine which folders to read from
    if tb_config.folder_filter:
        # Validate folder upfront (will error if ambiguous)
        try:
            server, folder_name = reader.resolve_folder(tb_config.folder_filter)
            folders = [f"{server}:{folder_name}"]
            logger.info(f"Reading from folder: {folder_name} (from {server})")
        except ValueError as e:
            logger.error(str(e))
            return
    else:
        # Get all folders with server prefix
        folders = reader.list_folders_qualified()

    # Collect sample emails
    sample_limit = tb_config.init_sample_limit
    all_emails = []

    for folder_spec in folders:
        # Use percentage or count based on sample_limit type
        if isinstance(sample_limit, float) and sample_limit < 1:
            # Percentage-based: use random sampling
            emails = list(reader.read_folder_random(folder_spec, sample_limit))
            logger.info(f"Sampled {len(emails)} emails ({sample_limit:.0%}) from {folder_spec}")
        elif tb_config.random_sample:
            # Random sampling with count limit
            limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
            emails = list(reader.read_folder_random(folder_spec, limit))
        else:
            # Sequential sampling
            limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
            emails = list(reader.read_folder(folder_spec, limit=limit))

        for email in emails:
            all_emails.append({
                "subject": email.subject,
                "from_addr": email.from_addr,
                "body": email.body_text[:300],
            })

    if not all_emails:
        logger.error("No emails found to analyze")
        return

    logger.info(f"Collected {len(all_emails)} emails, processing in batches...")

    # Process in batches, refining categories iteratively
    batch_size = 100
    categories: list[SuggestedFolder] = []
    all_assignments: list[dict] = []

    async with OllamaClient(config.ollama) as llm:
        for batch_num, start_idx in enumerate(range(0, len(all_emails), batch_size), 1):
            batch = all_emails[start_idx:start_idx + batch_size]

            categories, assignments = await llm.refine_folder_structure(
                batch,
                categories,
                batch_num,
                batch_size,
            )

            all_assignments.extend(assignments)
            logger.info(f"Batch {batch_num}: {len(categories)} categories after processing {len(batch)} emails")

        # Normalize categories to merge duplicates
        logger.info("Normalizing categories to merge duplicates...")
        categories, rename_map = await llm.normalize_categories(categories)
        logger.info(f"After normalization: {len(categories)} categories")

        # Apply rename map to all assignments
        for assignment in all_assignments:
            old_cat = assignment.get("category", "")
            if old_cat in rename_map:
                assignment["category"] = rename_map[old_cat]

    # Display final categories
    print(f"\nProcessed {len(all_emails)} emails in {batch_num} batches.")
    print(f"Final folder structure ({len(categories)} categories):\n")

    for i, folder in enumerate(categories, 1):
        print(f"  {i}. {folder.name}")
        print(f"     {folder.description}")

    print()

    # Count assignments per category
    category_counts: dict[str, int] = {}
    for assignment in all_assignments:
        cat = assignment.get("category", "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    if category_counts:
        print("Email distribution:")
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")
        print()

    # Save categories to file
    categories_path = Path(config.database.categories_file)
    new_categories = [
        Category(name=folder.name, description=folder.description)
        for folder in categories
    ]
    save_categories(new_categories, categories_path)
    print(f"Saved {len(categories)} categories to {categories_path}")


async def run_init_folders(config: Config) -> None:
    """Run folder initialization mode."""
    await init_folders_from_samples(config)


def apply_cli_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Apply command-line overrides to config."""
    if getattr(args, "db_path", None):
        config.database.path = args.db_path
    if getattr(args, "ollama_url", None):
        config.ollama.base_url = args.ollama_url
    if getattr(args, "ollama_model", None):
        config.ollama.model = args.ollama_model
    if getattr(args, "thunderbird_profile", None):
        config.thunderbird.profile_path = args.thunderbird_profile
    if getattr(args, "thunderbird_folder", None):
        config.thunderbird.folder_filter = args.thunderbird_folder
    if getattr(args, "samples_per_folder", None) is not None:
        config.thunderbird.samples_per_folder = args.samples_per_folder
    if getattr(args, "random", False):
        config.thunderbird.random_sample = True

    # Handle --limit (used for import_limit and init_sample_limit)
    import_limit = getattr(args, "import_limit", None)
    if import_limit is not None:
        # If >= 1, treat as integer count; if < 1, treat as percentage
        if import_limit >= 1:
            config.thunderbird.import_limit = int(import_limit)
            config.thunderbird.init_sample_limit = int(import_limit)
        else:
            config.thunderbird.import_limit = import_limit
            config.thunderbird.init_sample_limit = import_limit

    return config


def reset_database(db_path: Path) -> None:
    """Delete the database file to start fresh."""
    if db_path.exists():
        db_path.unlink()
        logger.info(f"Deleted database: {db_path}")
    else:
        logger.info(f"Database does not exist: {db_path}")


async def cleanup_thunderbird_folders(
    config: Config,
    db: Database,
    target_account: str = "local",
) -> None:
    """Delete classification folders from Thunderbird via extension.

    Queries Thunderbird for folders matching category names and deletes them.

    Args:
        config: Application configuration
        db: Database instance (unused but kept for API consistency)
        target_account: Target account: 'local', 'imap', or account ID
    """
    from .protocol import Action
    from .websocket_server import WebSocketServer

    # Load category names from categories.txt
    categories = load_categories(config.database.categories_file)
    category_names = {cat.name for cat in categories}

    if not category_names:
        logger.info("No categories defined")
        return

    logger.info(f"Will check for {len(category_names)} category folders in {target_account}")

    # Start WebSocket server
    ws_config = config.websocket
    if not ws_config.enabled:
        logger.error("WebSocket is not enabled in config")
        return

    server = WebSocketServer(ws_config, db, config.database.categories_file)
    server_task = asyncio.create_task(server.start())

    logger.info(f"WebSocket server started on ws://{ws_config.host}:{ws_config.port}")
    logger.info("Waiting for Thunderbird extension to connect...")

    try:
        # Wait for extension to connect
        for _ in range(30):
            if server.is_connected:
                break
            await asyncio.sleep(1)
        else:
            logger.error("Timeout waiting for extension to connect")
            return

        logger.info("Extension connected!")

        # Query Thunderbird for existing folders
        response = await server.send_request(
            Action.LIST_FOLDERS,
            {"accountId": target_account},
            timeout=10,
        )

        if not response or not response.ok:
            error = response.error if response else "No response"
            logger.error(f"Failed to list folders: {error}")
            return

        # Find folders that match category names
        # Each folder is {accountId, path, name, type}
        existing_folders = (response.result or {}).get("folders", [])
        folders_to_delete = [f["name"] for f in existing_folders if f.get("name") in category_names]

        if not folders_to_delete:
            logger.info("No classification folders found to delete")
            return

        logger.info(f"Found {len(folders_to_delete)} classification folders to delete")

        # Delete each folder
        deleted = 0
        failed = 0

        for folder_name in sorted(folders_to_delete):
            response = await server.send_request(
                Action.DELETE_FOLDER,
                {"accountId": target_account, "path": folder_name},
                timeout=10,
            )

            if response and response.ok:
                logger.info(f"  Deleted: {folder_name}")
                deleted += 1
            else:
                error = response.error if response else "No response"
                logger.warning(f"  Failed to delete {folder_name}: {error}")
                failed += 1

        logger.info(f"Cleanup complete: {deleted} deleted, {failed} failed")

    finally:
        await server.stop()
        server_task.cancel()


def upload_to_imap(
    config: Config,
    db: Database,
    dry_run: bool = False,
    folder_filter: str | None = None,
) -> None:
    """Upload classified emails to their target folders on IMAP.

    Args:
        config: Application configuration
        db: Database connection
        dry_run: If True, show what would be uploaded without uploading
        folder_filter: If provided, only upload emails classified to this folder
    """
    from .thunderbird import get_raw_email

    db.connect()
    db.init_schema()

    try:
        # Get classification counts
        counts = db.get_classification_counts()
        if not counts:
            logger.info("No classified emails to upload")
            return

        # Filter to specific folder if requested
        if folder_filter:
            if folder_filter not in counts:
                logger.error(f"No emails classified to folder: {folder_filter}")
                logger.info(f"Available folders: {', '.join(sorted(counts.keys()))}")
                return
            counts = {folder_filter: counts[folder_filter]}

        logger.info(f"Found {sum(counts.values())} classified emails in {len(counts)} folders")

        if dry_run:
            print("\nDry run - would upload:")
            for folder, count in sorted(counts.items(), key=lambda x: -x[1]):
                print(f"  {folder}: {count} emails")
            return

        # Connect to IMAP
        mailbox = ImapMailbox(config.imap)
        mailbox.connect()

        try:
            uploaded = 0
            skipped = 0
            errors = 0

            for folder, count in counts.items():
                logger.info(f"Processing folder: {folder} ({count} emails)")

                # Ensure folder exists
                mailbox.ensure_folder(folder)

                # Get emails for this classification
                emails = db.get_emails_by_classification(folder)

                for email_record in emails:
                    if not email_record.mbox_path:
                        logger.debug(f"Skipping {email_record.message_id}: no mbox_path")
                        skipped += 1
                        continue

                    # Get raw email from mbox
                    raw_email = get_raw_email(email_record.mbox_path, email_record.message_id)
                    if not raw_email:
                        logger.warning(f"Could not retrieve {email_record.message_id} from {email_record.mbox_path}")
                        errors += 1
                        continue

                    # Upload to IMAP
                    try:
                        uid = mailbox.append_email(folder, raw_email, flags=(r"\Seen",))
                        uploaded += 1
                        if uid:
                            logger.debug(f"Uploaded {email_record.message_id} to {folder} (UID: {uid})")
                        else:
                            logger.debug(f"Uploaded {email_record.message_id} to {folder}")
                    except Exception as e:
                        logger.error(f"Failed to upload {email_record.message_id}: {e}")
                        errors += 1

            logger.info(f"Upload complete: {uploaded} uploaded, {skipped} skipped, {errors} errors")

        finally:
            mailbox.disconnect()

    finally:
        db.close()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a parser."""
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to configuration file",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Override database path",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        help="Override Ollama base URL",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        help="Override Ollama model name",
    )


def add_thunderbird_args(parser: argparse.ArgumentParser) -> None:
    """Add Thunderbird-related arguments to a parser."""
    parser.add_argument(
        "--profile",
        type=str,
        dest="thunderbird_profile",
        help="Thunderbird profile path",
    )
    parser.add_argument(
        "--folder",
        type=str,
        dest="thunderbird_folder",
        help="Process only this folder (e.g., INBOX or server.com:INBOX)",
    )


def add_limit_args(parser: argparse.ArgumentParser) -> None:
    """Add limit-related arguments to a parser."""
    parser.add_argument(
        "--limit",
        type=float,
        dest="import_limit",
        help="Max emails: integer for count, fraction for percentage (0.1 = 10%%)",
    )
    parser.add_argument(
        "--random",
        action="store_true",
        help="Randomly sample emails instead of sequential",
    )


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Mailmap email classification system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # daemon - Run IMAP daemon (default)
    daemon_parser = subparsers.add_parser("daemon", help="Run IMAP listener daemon")
    add_common_args(daemon_parser)

    # learn - Learn categories from existing folders (saves to categories.txt)
    learn_parser = subparsers.add_parser("learn", help="Learn categories from existing Thunderbird folders")
    add_common_args(learn_parser)
    add_thunderbird_args(learn_parser)

    # classify - Bulk classify emails from Thunderbird
    classify_parser = subparsers.add_parser("classify", help="Bulk classify emails from Thunderbird")
    add_common_args(classify_parser)
    add_thunderbird_args(classify_parser)
    add_limit_args(classify_parser)
    classify_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy classified emails to target folders via Thunderbird extension",
    )
    classify_parser.add_argument(
        "--move",
        action="store_true",
        help="Move classified emails to target folders via Thunderbird extension",
    )
    classify_parser.add_argument(
        "--target-account",
        type=str,
        default="local",
        help="Target account for folders: 'local' (default), 'imap' (first IMAP), or account ID",
    )

    # init - Initialize folder structure
    init_parser = subparsers.add_parser("init", help="Analyze emails and suggest folder structure")
    add_common_args(init_parser)
    add_thunderbird_args(init_parser)
    add_limit_args(init_parser)

    # upload - Upload to IMAP
    upload_parser = subparsers.add_parser("upload", help="Upload classified emails to IMAP folders")
    add_common_args(upload_parser)
    upload_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without uploading",
    )
    upload_parser.add_argument(
        "--folder",
        type=str,
        dest="upload_folder",
        help="Only upload emails classified to this folder",
    )

    # list - List classifications
    list_parser = subparsers.add_parser("list", help="List classification results")
    add_common_args(list_parser)
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum results to show (default: 50)",
    )

    # categories - List categories
    categories_parser = subparsers.add_parser("categories", help="List classification categories")
    add_common_args(categories_parser)

    # summary - Classification summary
    summary_parser = subparsers.add_parser("summary", help="Show classification summary with counts")
    add_common_args(summary_parser)

    # clear - Clear classifications
    clear_parser = subparsers.add_parser("clear", help="Clear email classifications")
    add_common_args(clear_parser)
    clear_parser.add_argument(
        "--folder",
        type=str,
        help="Only clear emails from this source folder",
    )

    # reset - Reset database
    reset_parser = subparsers.add_parser("reset", help="Delete database and start fresh")
    add_common_args(reset_parser)

    # cleanup - Delete classification folders from Thunderbird
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Delete classification folders from Thunderbird Local Folders"
    )
    add_common_args(cleanup_parser)
    cleanup_parser.add_argument(
        "--target-account",
        type=str,
        default="local",
        help="Target account: 'local' (default), 'imap', or account ID",
    )

    args = parser.parse_args()

    # Default to daemon if no command specified
    if not args.command:
        args.command = "daemon"

    if not args.config.exists():
        logger.error(f"Configuration file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)

    # Handle reset before creating Database object
    if args.command == "reset":
        reset_database(Path(config.database.path))
        sys.exit(0)

    db = Database(config.database.path)

    if args.command == "list":
        limit = getattr(args, "limit", 50)
        list_classifications(db, limit=limit)
    elif args.command == "categories":
        list_categories_cmd(config)
    elif args.command == "summary":
        summary_cmd(db)
    elif args.command == "clear":
        folder = getattr(args, "folder", None)
        clear_cmd(db, folder)
    elif args.command == "init":
        asyncio.run(run_init_folders(config))
    elif args.command == "learn":
        asyncio.run(run_learn_folders(config))
    elif args.command == "classify":
        copy_mode = getattr(args, "copy", False)
        move_mode = getattr(args, "move", False)
        target_account = getattr(args, "target_account", "local")
        asyncio.run(run_bulk_classify(config, db, copy=copy_mode, move=move_mode, target_account=target_account))
    elif args.command == "upload":
        dry_run = getattr(args, "dry_run", False)
        folder_filter = getattr(args, "upload_folder", None)
        upload_to_imap(config, db, dry_run=dry_run, folder_filter=folder_filter)
    elif args.command == "cleanup":
        target_account = getattr(args, "target_account", "local")
        asyncio.run(cleanup_thunderbird_folders(config, db, target_account=target_account))
    elif args.command == "daemon":
        asyncio.run(run_daemon(config, db))


if __name__ == "__main__":
    main()
