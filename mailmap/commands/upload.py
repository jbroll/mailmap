"""Upload and cleanup commands - IMAP upload and folder cleanup."""

from __future__ import annotations

import logging

from ..categories import load_categories
from ..config import Config
from ..database import Database
from ..imap_client import ImapMailbox

logger = logging.getLogger("mailmap")


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
    from ..thunderbird import get_raw_email

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

                    # Get raw email - check if IMAP source (numeric UID) or Thunderbird (file path)
                    if email_record.mbox_path.isdigit():
                        # IMAP source: mbox_path is UID, folder_id is source folder
                        uid = int(email_record.mbox_path)
                        source_folder = email_record.folder_id
                        raw_email = mailbox.fetch_raw_email(uid, source_folder)
                    else:
                        # Thunderbird source: mbox_path is file path
                        raw_email = get_raw_email(email_record.mbox_path, email_record.message_id)

                    if not raw_email:
                        logger.warning(f"Could not retrieve {email_record.message_id} from {email_record.mbox_path}")
                        errors += 1
                        continue

                    # Upload to IMAP
                    try:
                        new_uid = mailbox.append_email(folder, raw_email, flags=(r"\Seen",))
                        uploaded += 1
                        if new_uid:
                            logger.debug(f"Uploaded {email_record.message_id} to {folder} (UID: {new_uid})")
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


async def cleanup_folders(
    config: Config,
    db: Database,
    target_account: str = "local",
    websocket_port: int | None = None,
) -> None:
    """Delete classification folders from target.

    Queries the target for folders matching category names and deletes them.

    Args:
        config: Application configuration
        db: Database instance (unused but kept for API consistency)
        target_account: Target account: 'local', 'imap', or server name
        websocket_port: If provided, use WebSocket on this port
    """
    from ..targets import select_target

    # Load category names from categories.txt
    categories = load_categories(config.database.categories_file)
    category_names = {cat.name for cat in categories}

    if not category_names:
        logger.info("No categories defined")
        return

    logger.info(f"Will check for {len(category_names)} category folders in {target_account}")

    # Select and connect to target
    try:
        target = select_target(config, target_account, websocket_port)
    except ValueError as e:
        logger.error(str(e))
        return

    async with target:
        logger.info(f"Using {target.target_type} target")

        # List folders on target
        existing_folders = await target.list_folders()

        # Find folders that match category names
        folders_to_delete = [f for f in existing_folders if f in category_names]

        if not folders_to_delete:
            logger.info("No classification folders found to delete")
            return

        logger.info(f"Found {len(folders_to_delete)} classification folders to delete")

        # Delete each folder
        deleted = 0
        failed = 0

        for folder_name in sorted(folders_to_delete):
            success = await target.delete_folder(folder_name)
            if success:
                logger.info(f"  Deleted: {folder_name}")
                deleted += 1
            else:
                logger.warning(f"  Failed to delete: {folder_name}")
                failed += 1

        logger.info(f"Cleanup complete: {deleted} deleted, {failed} failed")


# Keep old name for backwards compatibility
async def cleanup_thunderbird_folders(
    config: Config,
    db: Database,
    target_account: str = "local",
) -> None:
    """Deprecated: Use cleanup_folders instead."""
    await cleanup_folders(config, db, target_account, websocket_port=9753)
