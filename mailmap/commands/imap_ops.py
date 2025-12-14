"""IMAP operations commands - folder and email management."""

from __future__ import annotations

import logging

from ..config import Config
from ..imap_client import ImapMailbox

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
    mailbox = ImapMailbox(config.imap)
    mailbox.connect()

    try:
        email = mailbox.fetch_email(uid, folder)
        if not email:
            logger.error(f"Email UID {uid} not found in {folder}")
            return

        print(f"From: {email.from_addr}")
        print(f"Subject: {email.subject}")
        print(f"Message-ID: {email.message_id}")
        print(f"Folder: {email.folder}")
        print(f"UID: {email.uid}")
        print("-" * 60)
        print(email.body_text or "(no body)")
    finally:
        mailbox.disconnect()


def create_folder_cmd(config: Config, folder: str) -> None:
    """Create a folder on IMAP server.

    Args:
        config: Application configuration
        folder: Folder name to create
    """
    mailbox = ImapMailbox(config.imap)
    mailbox.connect()

    try:
        if mailbox.folder_exists(folder):
            logger.info(f"Folder already exists: {folder}")
        else:
            mailbox.create_folder(folder)
            logger.info(f"Created folder: {folder}")
    finally:
        mailbox.disconnect()


def delete_folder_cmd(config: Config, folder: str) -> None:
    """Delete a folder from IMAP server.

    Args:
        config: Application configuration
        folder: Folder name to delete
    """
    mailbox = ImapMailbox(config.imap)
    mailbox.connect()

    try:
        if not mailbox.folder_exists(folder):
            logger.error(f"Folder does not exist: {folder}")
            return

        mailbox.client.delete_folder(folder)
        logger.info(f"Deleted folder: {folder}")
    finally:
        mailbox.disconnect()


def move_email_cmd(config: Config, folder: str, uid: int, dest: str) -> None:
    """Move an email to another folder.

    Args:
        config: Application configuration
        folder: Source folder
        uid: Email UID
        dest: Destination folder
    """
    mailbox = ImapMailbox(config.imap)
    mailbox.connect()

    try:
        mailbox.ensure_folder(dest)
        mailbox.move_email(uid, folder, dest)
        logger.info(f"Moved UID {uid} from {folder} to {dest}")
    finally:
        mailbox.disconnect()


def copy_email_cmd(config: Config, folder: str, uid: int, dest: str) -> None:
    """Copy an email to another folder.

    Args:
        config: Application configuration
        folder: Source folder
        uid: Email UID
        dest: Destination folder
    """
    mailbox = ImapMailbox(config.imap)
    mailbox.connect()

    try:
        mailbox.ensure_folder(dest)
        mailbox.select_folder(folder)
        mailbox.client.copy([uid], dest)
        logger.info(f"Copied UID {uid} from {folder} to {dest}")
    finally:
        mailbox.disconnect()
