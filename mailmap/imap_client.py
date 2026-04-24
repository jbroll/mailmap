"""IMAP client for mailmap — thin wrapper over imap_tool.

Extends imap_tool.ImapClient with mailmap-specific helpers, and provides an
async callback-based listener (ImapListener) that reuses the same client.
Plain dicts from imap_tool.email_utils.parse_email are used throughout — no
EmailMessage dataclass.
"""

import asyncio
import logging
from collections.abc import Callable

from imap_tool.client import ImapClient as ToolImapClient
from imap_tool.email_utils import (
    decode_mime_header,  # re-exported for mbox.py
    extract_attachments,  # re-exported
    extract_body,  # re-exported
)

from .config import ImapConfig

logger = logging.getLogger("mailmap")

__all__ = [
    "ImapClient",
    "ImapListener",
    "decode_mime_header",
    "extract_attachments",
    "extract_body",
]


class ImapClient(ToolImapClient):
    """Mailmap's IMAP client: imap_tool.ImapClient plus mailmap-specific helpers.

    Constructed from mailmap's ImapConfig (with MAILMAP_* env credentials).
    """

    def __init__(self, config: ImapConfig):
        super().__init__(config.to_tool_config())

    def fetch_all_headers(self, folder: str) -> list[tuple[int, str, str, str]]:
        """Fetch Message-ID, From, and Subject for every message in a folder.

        Returns [(uid, message_id, from_addr, subject), ...] in a single IMAP
        FETCH call — much faster than one fetch_email() call per message.
        """
        self.select_folder(folder)
        uids = list(self.raw.search(["ALL"]))
        if not uids:
            return []

        messages = self.raw.fetch(
            uids,
            ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)]"],
        )

        results: list[tuple[int, str, str, str]] = []
        for uid in uids:
            data = messages.get(uid, {})
            raw = data.get(b"BODY[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)]", b"")
            if not raw:
                continue
            header_str = raw.decode("utf-8", errors="replace")
            # Unfold RFC 5322 continuation lines
            header_str = header_str.replace("\r\n ", " ").replace("\r\n\t", " ")
            header_str = header_str.replace("\n ", " ").replace("\n\t", " ")

            msg_id = from_addr = subject = ""
            for line in header_str.split("\n"):
                low = line.lower()
                if low.startswith("message-id:"):
                    msg_id = line.split(":", 1)[1].strip()
                elif low.startswith("from:"):
                    from_addr = line.split(":", 1)[1].strip()
                elif low.startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()

            if msg_id:
                results.append((uid, msg_id, from_addr, subject))

        return results

    def fetch_all_message_ids(self, folder: str) -> list[str]:
        """Fetch all Message-ID headers from a folder."""
        self.select_folder(folder)
        uids = self.raw.search(["ALL"])
        if not uids:
            return []

        messages = self.raw.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
        message_ids = []
        for data in messages.values():
            header_data = data.get(b"BODY[HEADER.FIELDS (MESSAGE-ID)]", b"")
            if not header_data:
                continue
            header_str = header_data.decode("utf-8", errors="replace")
            # Unfold RFC 5322 continuation lines
            header_str = header_str.replace("\r\n ", " ").replace("\r\n\t", " ")
            header_str = header_str.replace("\n ", " ").replace("\n\t", " ")
            for line in header_str.split("\n"):
                if line.lower().startswith("message-id:"):
                    msg_id = line.split(":", 1)[1].strip()
                    if msg_id:
                        message_ids.append(msg_id)
                    break
        return message_ids


class ImapListener:
    """Async IMAP listener with IDLE + polling, callback-based.

    Delivers parsed email dicts (imap_tool.parse_email format) to the callback.
    Reconnects with exponential backoff on failure.
    """

    INITIAL_RETRY_DELAY = 5
    MAX_RETRY_DELAY = 300
    BACKOFF_MULTIPLIER = 2

    def __init__(self, config: ImapConfig):
        self.config = config
        self._running = False
        self._last_uids: dict[str, int] = {}

    def _calculate_backoff(self, attempt: int) -> float:
        delay = self.INITIAL_RETRY_DELAY * (self.BACKOFF_MULTIPLIER ** attempt)
        return min(delay, self.MAX_RETRY_DELAY)

    async def watch_folder_idle(
        self,
        folder: str,
        callback: Callable[[dict], None],
    ) -> None:
        """Watch a folder with IMAP IDLE, reconnecting on failure."""
        attempt = 0

        while self._running:
            client = ImapClient(self.config)
            try:
                await self._run_idle_loop(client, folder, callback)
                break
            except Exception as e:
                client.disconnect()
                if not self._running:
                    break

                delay = self._calculate_backoff(attempt)
                logger.error(f"IMAP connection error on {folder}: {e}")
                logger.info(f"Reconnecting in {delay:.0f}s (attempt {attempt + 1})...")
                await asyncio.sleep(delay)
                attempt += 1

    async def _run_idle_loop(
        self,
        client: ImapClient,
        folder: str,
        callback: Callable[[dict], None],
    ) -> None:
        def run_idle():
            client.connect()
            logger.info(f"Connected to {self.config.host}, watching {folder} with IDLE")
            try:
                uids = client.fetch_uids(folder, limit=1)
                self._last_uids[folder] = uids[-1] if uids else 0
                logger.info(f"IDLE started on {folder} (last UID: {self._last_uids[folder]})")
                client.select_folder(folder)

                while self._running:
                    responses = client.idle_check(timeout=30)
                    for response in responses:
                        if len(response) >= 2 and response[1] == b"EXISTS":
                            new_uids = client.get_new_uids_since(
                                folder, self._last_uids.get(folder, 0)
                            )
                            for uid in new_uids:
                                msg = client.fetch_email(uid, folder)
                                if msg:
                                    callback(msg)
                                    self._last_uids[folder] = uid
            finally:
                client.disconnect()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_idle)

    async def poll_folder(
        self,
        folder: str,
        callback: Callable[[dict], None],
        interval: int = 300,
    ) -> None:
        """Poll a folder periodically for new messages."""
        logger.info(f"Polling {folder} every {interval}s")
        attempt = 0

        while self._running:
            try:
                messages = await self._check_folder_once(folder)
                for msg in messages:
                    callback(msg)
                attempt = 0
                await asyncio.sleep(interval)
            except Exception as e:
                if not self._running:
                    break

                delay = self._calculate_backoff(attempt)
                logger.error(f"IMAP poll error on {folder}: {e}")
                logger.info(f"Retrying in {delay:.0f}s (attempt {attempt + 1})...")
                await asyncio.sleep(delay)
                attempt += 1

    async def _check_folder_once(self, folder: str) -> list[dict]:
        client = ImapClient(self.config)

        def check_folder():
            client.connect()
            try:
                uids = client.fetch_uids(folder, limit=1)
                if folder not in self._last_uids:
                    self._last_uids[folder] = uids[-1] if uids else 0
                    return []

                new_uids = client.get_new_uids_since(folder, self._last_uids[folder])
                messages = []
                for uid in new_uids:
                    msg = client.fetch_email(uid, folder)
                    if msg:
                        messages.append(msg)
                        self._last_uids[folder] = uid
                return messages
            finally:
                client.disconnect()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, check_folder)

    async def start(
        self,
        callback: Callable[[dict], None],
    ) -> None:
        """Start monitoring configured idle_folders only."""
        self._running = True
        tasks = []

        logger.info(f"Connecting to IMAP server {self.config.host}:{self.config.port}")

        for folder in self.config.idle_folders:
            tasks.append(self.watch_folder_idle(folder, callback))

        logger.info(
            f"Monitoring {len(self.config.idle_folders)} folders with IDLE: "
            f"{', '.join(self.config.idle_folders)}"
        )
        await asyncio.gather(*tasks)

    def stop(self) -> None:
        self._running = False
