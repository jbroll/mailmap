"""IMAP client for email monitoring."""

import asyncio
import contextlib
import email
import email.message
import logging
from collections.abc import Callable
from dataclasses import dataclass
from email.header import decode_header

from imapclient import IMAPClient

from .config import ImapConfig

logger = logging.getLogger("mailmap")


@dataclass
class EmailMessage:
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str
    uid: int
    attachments: list[dict] | None = None  # List of {filename, content_type, text_content}


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
                # Skip attachments - only get inline body text
                disposition = part.get("Content-Disposition", "")
                if "attachment" in disposition:
                    continue
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                disposition = part.get("Content-Disposition", "")
                if "attachment" in disposition:
                    continue
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


def extract_attachments(msg: email.message.Message) -> list[dict[str, str | None]]:
    """Extract attachment metadata and text content from email.

    Returns list of attachment info dicts with:
    - filename: Name of the attachment
    - content_type: MIME type
    - text_content: Extracted text for parseable types (.ics, .txt, .vcf)
    """
    attachments: list[dict[str, str | None]] = []

    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get("Content-Disposition", "")
        filename = part.get_filename()

        # Skip main body parts (inline text/html without filename)
        if (
            not filename
            and "attachment" not in disposition
            and content_type in ("text/plain", "text/html")
        ):
            continue

        # Get filename from Content-Type if not in Content-Disposition
        if not filename:
            # Try to get from content-type params
            name_param = part.get_param("name")
            if isinstance(name_param, str):
                filename = name_param
            elif isinstance(name_param, tuple):
                # Encoded parameter: (charset, language, value)
                filename = name_param[2]

        if not filename and content_type == "text/calendar":
            filename = "calendar.ics"

        if not filename:
            continue

        attachment_info = {
            "filename": filename,
            "content_type": content_type,
            "text_content": None,
        }

        # Parse text-based attachments
        parseable_types = (
            "text/calendar",
            "application/ics",
            "text/plain",
            "text/x-vcard",
            "text/vcard",
            "text/csv",
            "application/json",
            "text/xml",
            "application/xml",
        )
        if content_type in parseable_types:
            try:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")

                    # Apply format-specific parsing to extract key info
                    if content_type in ("text/calendar", "application/ics"):
                        text = _parse_ics_summary(text)
                    elif content_type == "text/csv":
                        text = _parse_csv_summary(text)
                    elif content_type == "application/json":
                        text = _parse_json_summary(text)
                    elif content_type in ("text/xml", "application/xml"):
                        text = _parse_xml_summary(text)

                    attachment_info["text_content"] = text[:500]  # Final size limit
            except Exception:
                pass

        attachments.append(attachment_info)

    return attachments


def _parse_ics_summary(ics_text: str) -> str:
    """Extract key fields from ICS calendar data."""
    lines = []
    for line in ics_text.split("\n"):
        line = line.strip()
        # Extract useful fields
        for prefix in ("SUMMARY:", "LOCATION:", "DTSTART", "ORGANIZER", "CATEGORIES:"):
            if line.upper().startswith(prefix):
                # Clean up the value
                value = line.split(":", 1)[-1].strip()
                if value:
                    lines.append(f"{prefix.rstrip(':')}: {value}")
                break
    return "\n".join(lines) if lines else "Calendar event"


def _parse_csv_summary(csv_text: str, max_rows: int = 3) -> str:
    """Extract headers and first few rows from CSV data."""
    import csv
    import io

    try:
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        if not rows:
            return "Empty CSV"

        # First row is usually headers
        headers = rows[0]
        summary_parts = [f"Columns: {', '.join(headers[:10])}"]  # Limit columns shown

        # Show a few data rows
        data_rows = rows[1 : max_rows + 1]
        if data_rows:
            summary_parts.append(f"Rows: {len(rows) - 1}")
            for i, row in enumerate(data_rows, 1):
                # Truncate each cell and limit columns
                cells = [str(c)[:30] for c in row[:5]]
                summary_parts.append(f"  Row {i}: {', '.join(cells)}")

        return "\n".join(summary_parts)
    except Exception:
        # Fallback: just show first few lines
        lines = csv_text.strip().split("\n")[:4]
        return "\n".join(line[:100] for line in lines)


def _parse_json_summary(json_text: str, max_keys: int = 10) -> str:
    """Extract key structure from JSON data."""
    import json

    try:
        data = json.loads(json_text)

        def summarize(obj: object, depth: int = 0) -> str:
            if depth > 2:  # Limit nesting depth
                return "..."
            if isinstance(obj, dict):
                keys = list(obj.keys())[:max_keys]
                if len(obj) > max_keys:
                    keys.append(f"... +{len(obj) - max_keys} more")
                parts = []
                for k in keys[:max_keys]:
                    if k.startswith("... +"):
                        parts.append(k)
                    else:
                        v = obj[k]
                        if isinstance(v, (dict, list)):
                            parts.append(f"{k}: {summarize(v, depth + 1)}")
                        else:
                            val_str = str(v)[:50]
                            parts.append(f"{k}: {val_str}")
                return "{" + ", ".join(parts) + "}"
            elif isinstance(obj, list):
                if not obj:
                    return "[]"
                return f"[{len(obj)} items: {summarize(obj[0], depth + 1)}]"
            else:
                return str(obj)[:50]

        return summarize(data)
    except Exception:
        # Fallback: first few lines
        lines = json_text.strip().split("\n")[:5]
        return "\n".join(line[:100] for line in lines)


def _parse_xml_summary(xml_text: str) -> str:
    """Extract structure summary from XML data."""
    import re

    try:
        # Find root element
        root_match = re.search(r"<(\w+)[>\s]", xml_text)
        root = root_match.group(1) if root_match else "unknown"

        # Find unique element names (limit to first portion of document)
        sample = xml_text[:2000]
        elements = re.findall(r"<(\w+)[>\s/]", sample)
        unique_elements = []
        seen = set()
        for el in elements:
            if el not in seen and el.lower() not in ("xml", "?xml"):
                unique_elements.append(el)
                seen.add(el)
                if len(unique_elements) >= 10:
                    break

        # Look for key value patterns like <amount>123</amount>
        values = []
        for pattern in (
            r"<(amount|total|price|quantity|date|name|id|status)>([^<]+)</",
            r"<(Amount|Total|Price|Quantity|Date|Name|ID|Status)>([^<]+)</",
        ):
            for match in re.finditer(pattern, sample, re.IGNORECASE):
                values.append(f"{match.group(1)}: {match.group(2)[:30]}")
                if len(values) >= 5:
                    break

        parts = [f"Root: <{root}>", f"Elements: {', '.join(unique_elements)}"]
        if values:
            parts.append(f"Values: {'; '.join(values)}")

        return "\n".join(parts)
    except Exception:
        # Fallback: first few lines
        lines = xml_text.strip().split("\n")[:5]
        return "\n".join(line[:100] for line in lines)


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
        # Use BODY.PEEK[] to avoid marking as read
        messages = self.client.fetch([uid], ["BODY.PEEK[]"])
        if uid not in messages:
            return None

        raw = messages[uid][b"BODY[]"]
        msg = email.message_from_bytes(raw)

        message_id = msg.get("Message-ID", f"<uid-{uid}@local>")
        subject = decode_mime_header(msg.get("Subject"))
        from_addr = decode_mime_header(msg.get("From"))
        body = extract_body(msg)
        attachments = extract_attachments(msg)

        return EmailMessage(
            message_id=message_id,
            folder=folder,
            subject=subject,
            from_addr=from_addr,
            body_text=body,
            uid=uid,
            attachments=attachments if attachments else None,
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
        # Use BODY.PEEK[] to avoid marking as read
        messages = self.client.fetch([uid], ["BODY.PEEK[]"])
        if uid not in messages:
            return None
        return messages[uid][b"BODY[]"]

    def fetch_recent_uids(self, folder: str, limit: int = 50) -> list[int]:
        """Fetch UIDs of recent messages in a folder."""
        self.select_folder(folder)
        uids = self.client.search(["ALL"])
        return list(uids[-limit:]) if uids else []

    def fetch_all_message_ids(self, folder: str) -> list[str]:
        """Fetch all Message-ID headers from a folder.

        Args:
            folder: The folder to fetch from

        Returns:
            List of Message-ID strings
        """
        self.select_folder(folder)
        uids = self.client.search(["ALL"])
        if not uids:
            return []

        # Fetch only the Message-ID header for efficiency
        messages = self.client.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
        message_ids = []
        for _uid, data in messages.items():
            header_data = data.get(b"BODY[HEADER.FIELDS (MESSAGE-ID)]", b"")
            if header_data:
                # Parse the header to extract Message-ID
                # Unfold header first (RFC 5322: folded headers have CRLF + whitespace)
                header_str = header_data.decode("utf-8", errors="replace")
                header_str = header_str.replace("\r\n ", " ").replace("\r\n\t", " ")
                header_str = header_str.replace("\n ", " ").replace("\n\t", " ")
                for line in header_str.split("\n"):
                    if line.lower().startswith("message-id:"):
                        msg_id = line.split(":", 1)[1].strip()
                        if msg_id:
                            message_ids.append(msg_id)
                        break
        return message_ids

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
    """Async IMAP listener that monitors folders for new emails.

    Includes automatic reconnection with exponential backoff on connection failures.
    """

    # Reconnection settings
    INITIAL_RETRY_DELAY = 5  # seconds
    MAX_RETRY_DELAY = 300  # 5 minutes max
    BACKOFF_MULTIPLIER = 2

    def __init__(self, config: ImapConfig):
        self.config = config
        self._running = False
        self._last_uids: dict[str, int] = {}

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = self.INITIAL_RETRY_DELAY * (self.BACKOFF_MULTIPLIER ** attempt)
        return min(delay, self.MAX_RETRY_DELAY)

    async def watch_folder_idle(
        self,
        folder: str,
        callback: Callable[[EmailMessage], None],
    ) -> None:
        """Watch a folder using IDLE for real-time notifications.

        Automatically reconnects on connection failures with exponential backoff.
        """
        attempt = 0

        while self._running:
            mailbox = ImapMailbox(self.config)
            try:
                await self._run_idle_loop(mailbox, folder, callback)
                # If we exit cleanly (self._running = False), break out
                break
            except Exception as e:
                mailbox.disconnect()
                if not self._running:
                    break

                delay = self._calculate_backoff(attempt)
                logger.error(f"IMAP connection error on {folder}: {e}")
                logger.info(f"Reconnecting in {delay:.0f}s (attempt {attempt + 1})...")
                await asyncio.sleep(delay)
                attempt += 1
            else:
                # Reset attempt counter on successful iteration
                attempt = 0

    async def _run_idle_loop(
        self,
        mailbox: ImapMailbox,
        folder: str,
        callback: Callable[[EmailMessage], None],
    ) -> None:
        """Run the IDLE monitoring loop (blocking, runs in executor)."""
        def run_idle():
            mailbox.connect()
            logger.info(f"Connected to {self.config.host}, watching {folder} with IDLE")
            try:
                uids = mailbox.fetch_recent_uids(folder, limit=1)
                self._last_uids[folder] = uids[-1] if uids else 0
                logger.info(f"IDLE started on {folder} (last UID: {self._last_uids[folder]})")

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
        """Poll a folder periodically for new messages.

        Automatically reconnects on connection failures with exponential backoff.
        """
        logger.info(f"Polling {folder} every {interval}s")
        attempt = 0

        while self._running:
            try:
                messages = await self._check_folder_once(folder)
                for msg in messages:
                    callback(msg)
                attempt = 0  # Reset on success
                await asyncio.sleep(interval)
            except Exception as e:
                if not self._running:
                    break

                delay = self._calculate_backoff(attempt)
                logger.error(f"IMAP poll error on {folder}: {e}")
                logger.info(f"Retrying in {delay:.0f}s (attempt {attempt + 1})...")
                await asyncio.sleep(delay)
                attempt += 1

    async def _check_folder_once(self, folder: str) -> list[EmailMessage]:
        """Check a folder for new messages (single poll)."""
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
        return await loop.run_in_executor(None, check_folder)

    async def start(
        self,
        callback: Callable[[EmailMessage], None],
    ) -> None:
        """Start monitoring configured idle_folders only."""
        self._running = True
        tasks = []

        logger.info(f"Connecting to IMAP server {self.config.host}:{self.config.port}")

        for folder in self.config.idle_folders:
            tasks.append(self.watch_folder_idle(folder, callback))

        logger.info(f"Monitoring {len(self.config.idle_folders)} folders with IDLE: {', '.join(self.config.idle_folders)}")
        await asyncio.gather(*tasks)

    def stop(self) -> None:
        """Stop the listener."""
        self._running = False
