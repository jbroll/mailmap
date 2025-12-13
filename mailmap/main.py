"""Main entry point and orchestration for mailmap."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .config import Config, load_config
from .database import Database, Email, Folder
from .imap_client import EmailMessage, ImapListener, ImapMailbox
from .llm import OllamaClient, SuggestedFolder
from .thunderbird import ThunderbirdReader

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

        folder_descriptions = self.db.get_folder_descriptions()
        if not folder_descriptions:
            logger.warning("No folder descriptions available, skipping classification")
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


async def sync_folders(config: Config, db: Database) -> None:
    """Sync folder list from IMAP server to database."""
    logger.info("Syncing folders from IMAP server...")
    mailbox = ImapMailbox(config.imap)

    loop = asyncio.get_event_loop()

    def fetch_folders():
        mailbox.connect()
        try:
            return mailbox.list_folders()
        finally:
            mailbox.disconnect()

    folders = await loop.run_in_executor(None, fetch_folders)

    for folder_name in folders:
        existing = db.get_folder(folder_name)
        if not existing:
            folder = Folder(folder_id=folder_name, name=folder_name)
            db.upsert_folder(folder)
            logger.info(f"Added folder: {folder_name}")

    logger.info(f"Synced {len(folders)} folders")


async def generate_folder_descriptions(config: Config, db: Database) -> None:
    """Generate descriptions for folders that don't have them."""
    logger.info("Generating folder descriptions...")
    folders = db.get_all_folders()

    mailbox = ImapMailbox(config.imap)
    loop = asyncio.get_event_loop()

    for folder in folders:
        if folder.description:
            continue

        def fetch_samples(folder_name=folder.folder_id):
            mailbox.connect()
            try:
                uids = mailbox.fetch_recent_uids(folder_name, limit=5)
                samples = []
                for uid in uids:
                    msg = mailbox.fetch_email(uid, folder_name)
                    if msg:
                        samples.append({
                            "subject": msg.subject,
                            "from_addr": msg.from_addr,
                            "body": msg.body_text[:500],
                        })
                return samples
            except Exception as e:
                logger.warning(f"Could not fetch samples from {folder_name}: {e}")
                return []
            finally:
                mailbox.disconnect()

        samples = await loop.run_in_executor(None, fetch_samples)

        if samples:
            async with OllamaClient(config.ollama) as llm:
                result = await llm.generate_folder_description(folder.folder_id, samples)
                folder.description = result.description
                folder.last_updated = datetime.now()
                db.upsert_folder(folder)
                logger.info(f"Generated description for {folder.folder_id}")
        else:
            folder.description = f"Folder named {folder.name}"
            folder.last_updated = datetime.now()
            db.upsert_folder(folder)


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
        await sync_folders(config, db)
        await generate_folder_descriptions(config, db)

        # Build list of services to run
        tasks = []

        # IMAP listener
        logger.info("Starting email listener...")
        tasks.append(run_listener(config, db))

        # WebSocket server (if enabled)
        if config.websocket.enabled:
            logger.info("Starting WebSocket server...")
            tasks.append(run_websocket_server(config.websocket, db))

        # Run all services concurrently
        await asyncio.gather(*tasks)
    finally:
        db.close()


async def import_from_thunderbird(config: Config, db: Database) -> None:
    """Import emails from Thunderbird profile, generate descriptions, and classify."""
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Initializing Thunderbird reader...")
    try:
        reader = ThunderbirdReader(profile_path, tb_config.server_filter)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # List available servers
    servers = reader.list_servers()
    logger.info(f"Available IMAP servers: {servers}")

    # Phase 1: Sync folders from Thunderbird
    logger.info("Phase 1: Syncing folders from Thunderbird...")
    all_folders = reader.list_folders()

    # Apply folder filter if specified
    if tb_config.folder_filter:
        folders = [f for f in all_folders if f == tb_config.folder_filter]
        if not folders:
            logger.error(f"Folder '{tb_config.folder_filter}' not found. Available: {all_folders}")
            return
        logger.info(f"Filtering to folder: {tb_config.folder_filter}")
    else:
        folders = all_folders

    for folder_name in folders:
        existing = db.get_folder(folder_name)
        if not existing:
            folder = Folder(folder_id=folder_name, name=folder_name)
            db.upsert_folder(folder)
            logger.info(f"Added folder: {folder_name}")
    logger.info(f"Synced {len(folders)} folders")

    # Phase 2: Generate folder descriptions from sample emails
    logger.info("Phase 2: Generating folder descriptions...")
    all_folders = db.get_all_folders()

    for folder in all_folders:
        if folder.description:
            continue

        samples = reader.get_sample_emails(
            folder.folder_id,
            count=tb_config.samples_per_folder,
        )

        if samples:
            sample_dicts = [
                {
                    "subject": s.subject,
                    "from_addr": s.from_addr,
                    "body": s.body_text[:500],
                }
                for s in samples
            ]
            async with OllamaClient(config.ollama) as llm:
                result = await llm.generate_folder_description(folder.folder_id, sample_dicts)
                folder.description = result.description
                folder.last_updated = datetime.now()
                db.upsert_folder(folder)
                logger.info(f"Generated description for {folder.folder_id}: {result.description[:60]}...")
        else:
            folder.description = f"Folder named {folder.name}"
            folder.last_updated = datetime.now()
            db.upsert_folder(folder)
            logger.info(f"No samples for {folder.folder_id}, using default description")

    # Phase 3: Import and classify emails
    logger.info("Phase 3: Importing and classifying emails...")
    folder_descriptions = db.get_folder_descriptions()

    if not folder_descriptions:
        logger.error("No folder descriptions available, cannot classify")
        return

    total_imported = 0
    total_classified = 0

    for folder_name in folders:
        logger.info(f"Processing folder: {folder_name}")
        folder_count = 0

        # Choose between random sampling and sequential reading
        if tb_config.random_sample and tb_config.import_limit:
            email_iterator = reader.read_folder_random(folder_name, tb_config.import_limit)
        else:
            email_iterator = reader.read_folder(folder_name, limit=tb_config.import_limit)

        for tb_email in email_iterator:
            # Check if already imported
            existing_email = db.get_email(tb_email.message_id)
            if existing_email:
                continue

            # Import email
            email_record = Email(
                message_id=tb_email.message_id,
                folder_id=tb_email.folder,
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
                    classification = await llm.classify_email(
                        tb_email.subject,
                        tb_email.from_addr,
                        tb_email.body_text,
                        folder_descriptions,
                    )
                db.update_classification(
                    tb_email.message_id, classification.predicted_folder, classification.confidence
                )
                total_classified += 1
            except Exception as e:
                logger.warning(f"Failed to classify {tb_email.message_id}: {e}")

        logger.info(f"  Imported {folder_count} emails from {folder_name}")

    logger.info(f"Import complete: {total_imported} imported, {total_classified} classified")


async def run_thunderbird_import(config: Config, db: Database) -> None:
    """Run Thunderbird import mode."""
    db.connect()
    db.init_schema()
    try:
        await import_from_thunderbird(config, db)
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


async def learn_from_existing_folders(config: Config, db: Database) -> None:
    """Learn classification categories from user's existing folder structure.

    This scans all non-system folders, samples emails from each, generates
    descriptions, and creates classification categories that preserve the
    user's manual organization.
    """
    from .thunderbird import read_mbox_random

    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Learning from existing folder structure...")
    try:
        reader = ThunderbirdReader(profile_path, tb_config.server_filter)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # Get all folders and filter out system folders
    all_folders = reader.list_folders()
    user_folders = [f for f in all_folders if not is_system_folder(f)]
    system_folders = [f for f in all_folders if is_system_folder(f)]

    logger.info(f"Found {len(all_folders)} total folders")
    logger.info(f"System folders (excluded): {system_folders}")
    logger.info(f"User folders to learn from: {user_folders}")

    if not user_folders:
        logger.warning("No user folders found to learn from")
        return

    # Determine sample limit (default to 10% if not specified)
    sample_limit = tb_config.import_limit if tb_config.import_limit else 0.1

    total_emails = 0
    total_folders = 0

    for folder_name in user_folders:
        logger.info(f"Processing folder: {folder_name}")

        # Sample emails from this folder
        if tb_config.random_sample or (isinstance(sample_limit, float) and sample_limit < 1):
            emails = list(reader.read_folder_random(folder_name, sample_limit))
        else:
            limit = int(sample_limit) if sample_limit else None
            emails = list(reader.read_folder(folder_name, limit=limit))

        if not emails:
            logger.info(f"  No emails in {folder_name}, skipping")
            continue

        # Generate folder description from samples
        sample_dicts = [
            {
                "subject": e.subject,
                "from_addr": e.from_addr,
                "body": e.body_text[:500],
            }
            for e in emails[:20]  # Use up to 20 for description generation
        ]

        async with OllamaClient(config.ollama) as llm:
            result = await llm.generate_folder_description(folder_name, sample_dicts)

        # Create folder in database with description
        folder = Folder(
            folder_id=folder_name,
            name=folder_name,
            description=result.description,
            last_updated=datetime.now(),
        )
        db.upsert_folder(folder)
        logger.info(f"  Created category '{folder_name}': {result.description[:60]}...")

        # Import sampled emails with their original folder as the classification
        for tb_email in emails:
            existing = db.get_email(tb_email.message_id)
            if existing:
                continue

            email_record = Email(
                message_id=tb_email.message_id,
                folder_id=tb_email.folder,
                subject=tb_email.subject,
                from_addr=tb_email.from_addr,
                mbox_path=tb_email.mbox_path,
                classification=folder_name,  # Pre-classified to original folder
                confidence=1.0,  # User's manual classification = 100% confidence
                processed_at=datetime.now(),
            )
            db.insert_email(email_record)
            total_emails += 1

        total_folders += 1
        logger.info(f"  Imported {len(emails)} emails from {folder_name}")

    logger.info(f"Learning complete: {total_folders} categories, {total_emails} training emails")


async def run_learn_folders(config: Config, db: Database) -> None:
    """Run learn-folders mode."""
    db.connect()
    db.init_schema()
    try:
        await learn_from_existing_folders(config, db)
    finally:
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


def list_folders_cmd(db: Database) -> None:
    """List folders and their descriptions."""
    db.connect()
    db.init_schema()
    try:
        folders = db.get_all_folders()

        if not folders:
            print("No folders found.")
            return

        print(f"{'Folder':<30} {'Description':<70}")
        print("-" * 102)

        for folder in folders:
            name = (folder.folder_id or "")[:28]
            desc = (folder.description or "No description")[:68]
            print(f"{name:<30} {desc:<70}")

        print(f"\nTotal: {len(folders)} folders")
    finally:
        db.close()


async def init_folders_from_samples(config: Config, db: Database) -> None:
    """Analyze sample emails iteratively in batches to build folder structure."""
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Initializing folder structure from email samples (iterative batching)...")

    try:
        reader = ThunderbirdReader(profile_path, tb_config.server_filter)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Reading emails from Thunderbird profile: {reader.profile_path}")

    # Determine which folders to read from
    all_folders = reader.list_folders()
    if tb_config.folder_filter:
        folders = [f for f in all_folders if f == tb_config.folder_filter]
        if not folders:
            logger.error(f"Folder '{tb_config.folder_filter}' not found. Available: {all_folders}")
            return
        logger.info(f"Reading from folder: {tb_config.folder_filter}")
    else:
        folders = all_folders

    # Collect sample emails
    sample_limit = tb_config.init_sample_limit
    all_emails = []

    for folder_name in folders:
        # Use percentage or count based on sample_limit type
        if isinstance(sample_limit, float) and sample_limit < 1:
            # Percentage-based: use random sampling
            emails = list(reader.read_folder_random(folder_name, sample_limit))
            logger.info(f"Sampled {len(emails)} emails ({sample_limit:.0%}) from {folder_name}")
        elif tb_config.random_sample:
            # Random sampling with count limit
            limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
            emails = list(reader.read_folder_random(folder_name, limit))
        else:
            # Sequential sampling
            limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
            emails = list(reader.read_folder(folder_name, limit=limit))

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

    # Create folders in database
    db.connect()
    db.init_schema()
    try:
        for folder in categories:
            db_folder = Folder(
                folder_id=folder.name,
                name=folder.name,
                description=folder.description,
                last_updated=datetime.now(),
            )
            db.upsert_folder(db_folder)
            logger.info(f"Created folder: {folder.name}")

        print(f"Created {len(categories)} folders in database.")
    finally:
        db.close()


async def run_init_folders(config: Config, db: Database) -> None:
    """Run folder initialization mode."""
    await init_folders_from_samples(config, db)


def apply_cli_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Apply command-line overrides to config."""
    if args.db_path:
        config.database.path = args.db_path
    if args.ollama_url:
        config.ollama.base_url = args.ollama_url
    if args.ollama_model:
        config.ollama.model = args.ollama_model
    if args.thunderbird_profile:
        config.thunderbird.profile_path = args.thunderbird_profile
    if args.thunderbird_server:
        config.thunderbird.server_filter = args.thunderbird_server
    if args.import_limit is not None:
        # If >= 1, treat as integer count; if < 1, treat as percentage
        if args.import_limit >= 1:
            config.thunderbird.import_limit = int(args.import_limit)
        else:
            config.thunderbird.import_limit = args.import_limit
    if args.samples_per_folder is not None:
        config.thunderbird.samples_per_folder = args.samples_per_folder
    if args.init_sample_limit is not None:
        # If >= 1, treat as integer count; if < 1, treat as percentage
        if args.init_sample_limit >= 1:
            config.thunderbird.init_sample_limit = int(args.init_sample_limit)
        else:
            config.thunderbird.init_sample_limit = args.init_sample_limit
    if args.thunderbird_folder:
        config.thunderbird.folder_filter = args.thunderbird_folder
    if args.random:
        config.thunderbird.random_sample = True
    return config


def reset_database(db_path: Path) -> None:
    """Delete the database file to start fresh."""
    if db_path.exists():
        db_path.unlink()
        logger.info(f"Deleted database: {db_path}")
    else:
        logger.info(f"Database does not exist: {db_path}")


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
    from .imap_client import ImapMailbox
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


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Mailmap email classification system")

    # Mode selection
    mode_group = parser.add_argument_group("modes")
    mode_group.add_argument(
        "--sync-folders",
        action="store_true",
        help="Sync folders and generate descriptions, then exit",
    )
    mode_group.add_argument(
        "--thunderbird",
        action="store_true",
        help="Import emails from Thunderbird profile, generate descriptions, and classify",
    )
    mode_group.add_argument(
        "--reset-db",
        action="store_true",
        help="Delete the database file and exit (for clean slate iteration)",
    )
    mode_group.add_argument(
        "--list",
        action="store_true",
        help="List classification results from the database",
    )
    mode_group.add_argument(
        "--list-folders",
        action="store_true",
        help="List folders and their descriptions",
    )
    mode_group.add_argument(
        "--init-folders",
        action="store_true",
        help="Analyze sample emails and suggest folder structure for initialization",
    )
    mode_group.add_argument(
        "--learn-folders",
        action="store_true",
        help="Learn categories from user's existing folders (excludes system folders)",
    )
    mode_group.add_argument(
        "--upload",
        action="store_true",
        help="Upload classified emails to IMAP server folders",
    )
    mode_group.add_argument(
        "--upload-dry-run",
        action="store_true",
        help="Show what would be uploaded without actually uploading",
    )
    mode_group.add_argument(
        "--upload-folder",
        type=str,
        metavar="FOLDER",
        help="Only upload emails classified to this folder (use with --upload or --upload-dry-run)",
    )

    # Config file
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to configuration file",
    )

    # Config overrides
    override_group = parser.add_argument_group("config overrides")
    override_group.add_argument(
        "--db-path",
        type=str,
        help="Override database path",
    )
    override_group.add_argument(
        "--ollama-url",
        type=str,
        help="Override Ollama base URL",
    )
    override_group.add_argument(
        "--ollama-model",
        type=str,
        help="Override Ollama model name",
    )
    override_group.add_argument(
        "--thunderbird-profile",
        type=str,
        help="Override Thunderbird profile path",
    )
    override_group.add_argument(
        "--thunderbird-server",
        type=str,
        help="Filter to specific IMAP server in Thunderbird",
    )
    override_group.add_argument(
        "--import-limit",
        type=float,
        help="Max emails: integer for count (2500), fraction for percentage (0.1 = 10%%)",
    )
    override_group.add_argument(
        "--samples-per-folder",
        type=int,
        help="Number of emails to sample for folder descriptions",
    )
    override_group.add_argument(
        "--init-sample-limit",
        type=float,
        help="Max emails for --init-folders: integer for count, fraction for percentage (0.2 = 20%%)",
    )
    override_group.add_argument(
        "--thunderbird-folder",
        type=str,
        help="Import only from this folder (e.g., INBOX)",
    )
    override_group.add_argument(
        "--random",
        action="store_true",
        help="Randomly sample emails instead of sequential (use with --import-limit)",
    )

    args = parser.parse_args()

    if not args.config.exists():
        logger.error(f"Configuration file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)

    # Handle reset-db before creating Database object
    if args.reset_db:
        reset_database(Path(config.database.path))
        sys.exit(0)

    db = Database(config.database.path)

    if args.list:
        list_classifications(db)
    elif args.list_folders:
        list_folders_cmd(db)
    elif args.init_folders:
        asyncio.run(run_init_folders(config, db))
    elif args.learn_folders:
        asyncio.run(run_learn_folders(config, db))
    elif args.thunderbird:
        asyncio.run(run_thunderbird_import(config, db))
    elif args.sync_folders:
        async def sync_only():
            db.connect()
            db.init_schema()
            try:
                await sync_folders(config, db)
                await generate_folder_descriptions(config, db)
            finally:
                db.close()
        asyncio.run(sync_only())
    elif args.upload or args.upload_dry_run:
        upload_to_imap(config, db, dry_run=args.upload_dry_run, folder_filter=args.upload_folder)
    else:
        asyncio.run(run_daemon(config, db))


if __name__ == "__main__":
    main()
