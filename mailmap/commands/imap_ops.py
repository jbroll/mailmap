"""IMAP operations commands - folder and email management."""

from __future__ import annotations

import logging

from ..config import Config
from ..imap_client import ImapClient

logger = logging.getLogger("mailmap")


async def list_folders_cmd(config: Config, source_type: str = "imap") -> None:
    """List folders with email counts.

    Args:
        config: Application configuration
        source_type: 'imap' or 'thunderbird'
    """
    from ..sources import select_source

    source = select_source(config, source_type)

    async with source:
        folders = await source.list_folders()

        print(f"{'Folder':<40} {'Count':>8}")
        print("-" * 50)

        for folder in sorted(folders):
            # Count emails in folder
            count = 0
            async for _ in source.read_emails(folder, limit=None):
                count += 1
            print(f"{folder:<40} {count:>8}")


async def list_emails_cmd(
    config: Config,
    folder: str,
    source_type: str = "imap",
    limit: int = 50,
) -> None:
    """List emails in a folder.

    Args:
        config: Application configuration
        folder: Folder name
        source_type: 'imap' or 'thunderbird'
        limit: Maximum emails to list
    """
    from ..sources import select_source

    source = select_source(config, source_type)

    async with source:
        print(f"{'UID':<8} {'From':<30} {'Subject':<50}")
        print("-" * 90)

        count = 0
        async for email in source.read_emails(folder, limit=limit):
            uid = email.source_ref if email.source_ref else "?"
            from_addr = (email.from_addr or "")[:28]
            subject = (email.subject or "")[:48]
            print(f"{uid:<8} {from_addr:<30} {subject:<50}")
            count += 1

        print(f"\nTotal: {count} emails")


async def read_email_cmd(
    config: Config,
    folder: str,
    uid: int,
) -> None:
    """Read and display an email.

    Args:
        config: Application configuration
        folder: Folder name
        uid: Email UID
    """
    client = ImapClient(config.imap)
    client.connect()

    try:
        msg = client.fetch_email(uid, folder)
        if not msg:
            logger.error(f"Email UID {uid} not found in {folder}")
            return

        print(f"From: {msg['from']}")
        print(f"Subject: {msg['subject']}")
        print(f"Message-ID: {msg['message_id']}")
        print(f"Folder: {msg['folder']}")
        print(f"UID: {msg['uid']}")
        print("-" * 60)
        print(msg.get("body") or "(no body)")
    finally:
        client.disconnect()


async def create_folder_cmd(
    config: Config,
    folder: str,
    target_account: str = "imap",
    websocket_port: int | None = None,
) -> None:
    """Create a folder on target.

    Args:
        config: Application configuration
        folder: Folder name to create
        target_account: Target account: 'local', 'imap', or server name
        websocket_port: If provided, use WebSocket on this port
    """
    from ..targets import select_target

    try:
        target = select_target(config, target_account, websocket_port)
    except ValueError as e:
        logger.error(str(e))
        return

    async with target:
        # Check if folder exists first
        existing = await target.list_folders()
        if folder in existing:
            logger.info(f"Folder already exists: {folder}")
        else:
            success = await target.create_folder(folder)
            if success:
                logger.info(f"Created folder: {folder}")
            else:
                logger.error(f"Failed to create folder: {folder}")


async def delete_folder_cmd(
    config: Config,
    folder: str,
    target_account: str = "imap",
    websocket_port: int | None = None,
) -> None:
    """Delete a folder from target.

    Args:
        config: Application configuration
        folder: Folder name to delete
        target_account: Target account: 'local', 'imap', or server name
        websocket_port: If provided, use WebSocket on this port
    """
    from ..targets import select_target

    try:
        target = select_target(config, target_account, websocket_port)
    except ValueError as e:
        logger.error(str(e))
        return

    async with target:
        # Check if folder exists first
        existing = await target.list_folders()
        if folder not in existing:
            logger.error(f"Folder does not exist: {folder}")
            return

        success = await target.delete_folder(folder)
        if success:
            logger.info(f"Deleted folder: {folder}")
        else:
            logger.error(f"Failed to delete folder: {folder}")


def move_email_cmd(config: Config, folder: str, uid: int, dest: str) -> None:
    """Move an email to another folder.

    Args:
        config: Application configuration
        folder: Source folder
        uid: Email UID
        dest: Destination folder
    """
    client = ImapClient(config.imap)
    client.connect()

    try:
        client.ensure_folder(dest)
        client.move_email(uid, folder, dest)
        logger.info(f"Moved UID {uid} from {folder} to {dest}")
    finally:
        client.disconnect()


def copy_email_cmd(config: Config, folder: str, uid: int, dest: str) -> None:
    """Copy an email to another folder.

    Args:
        config: Application configuration
        folder: Source folder
        uid: Email UID
        dest: Destination folder
    """
    client = ImapClient(config.imap)
    client.connect()

    try:
        client.ensure_folder(dest)
        client.copy_email(uid, folder, dest)
        logger.info(f"Copied UID {uid} from {folder} to {dest}")
    finally:
        client.disconnect()
