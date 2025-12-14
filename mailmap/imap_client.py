"""IMAP client for email monitoring."""

import asyncio
import contextlib
import email
import email.message
from collections.abc import Callable
from dataclasses import dataclass
from email.header import decode_header

from imapclient import IMAPClient

from .config import ImapConfig


@dataclass
class EmailMessage:
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str
    uid: int


def decode_mime_header(header: str | None) -> str:
    """Decode a MIME-encoded email header."""
    if header is None:
        return ""
    decoded_parts = decode_header(header)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def extract_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


class ImapMailbox:
    def __init__(self, config: ImapConfig):
        self.config = config
        self._client: IMAPClient | None = None

    def connect(self) -> None:
        """Connect to the IMAP server."""
        self._client = IMAPClient(
            self.config.host,
            port=self.config.port,
            ssl=self.config.use_ssl,
        )
        self._client.login(self.config.username, self.config.password)

    def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self._client:
            with contextlib.suppress(Exception):
                self._client.logout()
            self._client = None

    @property
    def client(self) -> IMAPClient:
        if self._client is None:
            raise RuntimeError("Not connected to IMAP server")
        return self._client

    def list_folders(self) -> list[str]:
        """List all available folders."""
        folders = self.client.list_folders()
        return [folder[2] for folder in folders]

    def select_folder(self, folder: str) -> dict:
        """Select a folder for operations."""
        return self.client.select_folder(folder)

    def fetch_email(self, uid: int, folder: str) -> EmailMessage | None:
        """Fetch a single email by UID."""
        self.select_folder(folder)
        messages = self.client.fetch([uid], ["RFC822"])
        if uid not in messages:
            return None

        raw = messages[uid][b"RFC822"]
        msg = email.message_from_bytes(raw)

        message_id = msg.get("Message-ID", f"<uid-{uid}@local>")
        subject = decode_mime_header(msg.get("Subject"))
        from_addr = decode_mime_header(msg.get("From"))
        body = extract_body(msg)

        return EmailMessage(
            message_id=message_id,
            folder=folder,
            subject=subject,
            from_addr=from_addr,
            body_text=body,
            uid=uid,
        )

    def fetch_raw_email(self, uid: int, folder: str) -> bytes | None:
        """Fetch raw email bytes by UID.

        Args:
            uid: The message UID
            folder: The folder containing the message

        Returns:
            Raw RFC822 email bytes, or None if not found
        """
        self.select_folder(folder)
        messages = self.client.fetch([uid], ["RFC822"])
        if uid not in messages:
            return None
        return messages[uid][b"RFC822"]

    def fetch_recent_uids(self, folder: str, limit: int = 50) -> list[int]:
        """Fetch UIDs of recent messages in a folder."""
        self.select_folder(folder)
        uids = self.client.search(["ALL"])
        return list(uids[-limit:]) if uids else []

    def get_new_uids_since(self, folder: str, last_uid: int) -> list[int]:
        """Get UIDs of messages newer than last_uid."""
        self.select_folder(folder)
        if last_uid > 0:
            uids = self.client.search(["UID", f"{last_uid + 1}:*"])
            return [uid for uid in uids if uid > last_uid]
        return list(self.client.search(["ALL"]))

    def idle_check(self, folder: str, timeout: int = 30) -> list[tuple]:
        """Wait for new messages using IDLE command."""
        self.select_folder(folder)
        self.client.idle()
        try:
            responses = self.client.idle_check(timeout=timeout)
            return responses
        finally:
            self.client.idle_done()

    def move_email(self, uid: int, from_folder: str, to_folder: str) -> None:
        """Move an email from one folder to another."""
        self.select_folder(from_folder)
        self.client.move([uid], to_folder)

    def append_email(
        self,
        folder: str,
        msg: bytes,
        flags: tuple[str, ...] = (),
        msg_time: float | None = None,
    ) -> int | None:
        """Append an email message to a folder.

        Args:
            folder: Target folder name
            msg: Raw email message as bytes (RFC822 format)
            flags: Optional tuple of flags (e.g., (r'\\Seen',))
            msg_time: Optional message timestamp (Unix timestamp)

        Returns:
            The UID of the appended message if server supports UIDPLUS, else None
        """
        from datetime import datetime

        dt = datetime.fromtimestamp(msg_time) if msg_time else None
        result = self.client.append(folder, msg, flags=flags, msg_time=dt)

        # IMAPClient returns the APPENDUID response if available
        # Format is typically: b'[APPENDUID <uidvalidity> <uid>] ...'
        if isinstance(result, bytes):
            result_str = result.decode("utf-8", errors="replace")
            if "APPENDUID" in result_str:
                # Extract UID from response like "[APPENDUID 123456 789]"
                import re
                match = re.search(r"APPENDUID\s+\d+\s+(\d+)", result_str)
                if match:
                    return int(match.group(1))
        return None

    def folder_exists(self, folder: str) -> bool:
        """Check if a folder exists on the server."""
        folders = self.list_folders()
        return folder in folders

    def create_folder(self, folder: str) -> bool:
        """Create a new folder on the server.

        Args:
            folder: Name of folder to create (can include hierarchy like "Parent/Child")

        Returns:
            True if folder was created, False if it already exists
        """
        if self.folder_exists(folder):
            return False
        self.client.create_folder(folder)
        return True

    def ensure_folder(self, folder: str) -> None:
        """Ensure a folder exists, creating it if necessary."""
        if not self.folder_exists(folder):
            self.client.create_folder(folder)


class ImapListener:
    """Async IMAP listener that monitors folders for new emails."""

    def __init__(self, config: ImapConfig):
        self.config = config
        self._running = False
        self._last_uids: dict[str, int] = {}

    async def watch_folder_idle(
        self,
        folder: str,
        callback: Callable[[EmailMessage], None],
    ) -> None:
        """Watch a folder using IDLE for real-time notifications."""
        mailbox = ImapMailbox(self.config)

        def run_idle():
            mailbox.connect()
            try:
                uids = mailbox.fetch_recent_uids(folder, limit=1)
                self._last_uids[folder] = uids[-1] if uids else 0

                while self._running:
                    responses = mailbox.idle_check(folder, timeout=30)
                    for response in responses:
                        if response[1] == b"EXISTS":
                            new_uids = mailbox.get_new_uids_since(
                                folder, self._last_uids.get(folder, 0)
                            )
                            for uid in new_uids:
                                msg = mailbox.fetch_email(uid, folder)
                                if msg:
                                    callback(msg)
                                    self._last_uids[folder] = uid
            finally:
                mailbox.disconnect()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_idle)

    async def poll_folder(
        self,
        folder: str,
        callback: Callable[[EmailMessage], None],
        interval: int = 300,
    ) -> None:
        """Poll a folder periodically for new messages."""
        mailbox = ImapMailbox(self.config)

        def check_folder():
            mailbox.connect()
            try:
                uids = mailbox.fetch_recent_uids(folder, limit=1)
                if folder not in self._last_uids:
                    self._last_uids[folder] = uids[-1] if uids else 0
                    return []

                new_uids = mailbox.get_new_uids_since(folder, self._last_uids[folder])
                messages = []
                for uid in new_uids:
                    msg = mailbox.fetch_email(uid, folder)
                    if msg:
                        messages.append(msg)
                        self._last_uids[folder] = uid
                return messages
            finally:
                mailbox.disconnect()

        loop = asyncio.get_event_loop()
        while self._running:
            messages = await loop.run_in_executor(None, check_folder)
            for msg in messages:
                callback(msg)
            await asyncio.sleep(interval)

    async def start(
        self,
        callback: Callable[[EmailMessage], None],
    ) -> None:
        """Start monitoring all configured folders."""
        self._running = True
        tasks = []

        for folder in self.config.idle_folders:
            tasks.append(self.watch_folder_idle(folder, callback))

        mailbox = ImapMailbox(self.config)
        mailbox.connect()
        all_folders = mailbox.list_folders()
        mailbox.disconnect()

        poll_folders = [f for f in all_folders if f not in self.config.idle_folders]
        for folder in poll_folders:
            tasks.append(
                self.poll_folder(folder, callback, self.config.poll_interval_seconds)
            )

        await asyncio.gather(*tasks)

    def stop(self) -> None:
        """Stop the listener."""
        self._running = False
