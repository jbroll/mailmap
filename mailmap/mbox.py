"""Mbox file reading utilities for Thunderbird cache."""

import logging
import mailbox
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .imap_client import decode_mime_header, extract_body

logger = logging.getLogger("mailmap")

# Headers to extract for spam detection
SPAM_HEADERS = [
    # Microsoft/Office 365
    "X-MS-Exchange-Organization-SCL",
    "X-Microsoft-Antispam",
    "X-Forefront-Antispam-Report",
    # SpamAssassin
    "X-Spam-Status",
    "X-Spam-Flag",
    "X-Spam-Score",
    "X-Spam-Level",
    # Rspamd
    "X-Rspamd-Score",
    "X-Rspamd-Action",
    "X-Spamd-Result",
    # Barracuda
    "X-Barracuda-Spam-Score",
    "X-Barracuda-Spam-Status",
    # SpamExperts / Spampanel
    "X-SpamExperts-Class",
    "X-SpamExperts-Outgoing-Class",
    "X-Spampanel-Outgoing-Class",
    # Proofpoint
    "X-Proofpoint-Spam-Details",
    # Cisco IronPort
    "X-IronPort-Anti-Spam-Result",
    # Trend Micro
    "X-TM-AS-Result",
    "X-TMASE-Result",
    # Mimecast
    "X-Mimecast-Spam-Score",
    # OVH
    "X-Ovh-Spam-Reason",
    "X-VR-SpamCause",
    # Generic
    "X-Spam",
    "X-IP-Spam-Verdict",
    # Authentication
    "Authentication-Results",
]


def extract_spam_headers(message) -> dict[str, str]:
    """Extract spam-related headers from an email message.

    Args:
        message: A mailbox.Message object

    Returns:
        Dict of header name -> value for spam-related headers
    """
    headers = {}
    for header_name in SPAM_HEADERS:
        value = message.get(header_name)
        if value:
            # Decode if needed and strip whitespace
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="replace")
                except Exception:
                    value = str(value)
            headers[header_name] = str(value).strip()
    return headers


@dataclass
class ThunderbirdEmail:
    """Email read from Thunderbird mbox cache."""
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str  # For LLM classification
    mbox_path: str  # For later retrieval of raw email
    headers: dict[str, str] | None = None  # Spam-related headers for filtering
    raw_bytes: bytes | None = None  # Raw email for cross-server transfers


def list_mbox_files(mail_dir: Path) -> list[tuple[str, Path]]:
    """List all mbox files in a mail directory, returning (folder_name, path) tuples."""
    mbox_files = []

    for item in mail_dir.rglob("*"):
        # mbox files have no extension, skip .msf (index) and .dat files
        if item.is_file() and not item.suffix and not item.name.startswith("."):
            # Check if it looks like an mbox file (has corresponding .msf or is non-empty)
            msf_file = item.with_suffix(".msf")
            if msf_file.exists() or item.stat().st_size > 0:
                # Derive folder name from path relative to mail_dir
                rel_path = item.relative_to(mail_dir)
                # Handle .sbd subdirectories (Thunderbird's subfolder convention)
                parts = []
                for part in rel_path.parts:
                    if part.endswith(".sbd"):
                        parts.append(part[:-4])  # Remove .sbd suffix
                    else:
                        parts.append(part)
                folder_name = "/".join(parts)
                mbox_files.append((folder_name, item))

    return mbox_files


def _open_mbox(mbox_path: Path) -> mailbox.mbox | None:
    """Open an mbox file with error handling."""
    try:
        return mailbox.mbox(mbox_path)
    except PermissionError as e:
        logger.warning(f"Permission denied opening mbox {mbox_path}: {e}")
    except FileNotFoundError as e:
        logger.warning(f"Mbox file not found {mbox_path}: {e}")
    except Exception as e:
        logger.error(f"Failed to open mbox {mbox_path}: {e}")
    return None


def _parse_message(
    message, folder_name: str, mbox_path_str: str, include_raw: bool = False
) -> ThunderbirdEmail | None:
    """Parse a mailbox message into ThunderbirdEmail.

    Args:
        message: mailbox.Message object
        folder_name: Name of the folder
        mbox_path_str: Path to mbox file as string
        include_raw: If True, capture raw bytes for cross-server transfers
    """
    try:
        message_id = message.get("Message-ID", f"<tb-{hash(str(message))}@local>")
        subject = decode_mime_header(message.get("Subject"))
        from_addr = decode_mime_header(message.get("From"))
        body = extract_body(message)
        headers = extract_spam_headers(message)
        raw_bytes = message.as_bytes() if include_raw else None

        return ThunderbirdEmail(
            message_id=message_id,
            folder=folder_name,
            subject=subject,
            from_addr=from_addr,
            body_text=body,
            mbox_path=mbox_path_str,
            headers=headers if headers else None,
            raw_bytes=raw_bytes,
        )
    except (UnicodeDecodeError, LookupError) as e:
        logger.debug(f"Encoding error parsing email in {folder_name}: {e}")
    except Exception as e:
        logger.warning(f"Failed to parse email in {folder_name}: {e}")
    return None


def read_mbox(
    mbox_path: Path, folder_name: str, limit: int | None = None, include_raw: bool = False
) -> Iterator[ThunderbirdEmail]:
    """Read emails from an mbox file.

    Args:
        mbox_path: Path to the mbox file
        folder_name: Name to assign to the folder
        limit: Maximum number of emails to read (None for all)
        include_raw: If True, capture raw bytes for cross-server transfers

    Yields:
        ThunderbirdEmail objects for each successfully parsed email
    """
    mbox = _open_mbox(mbox_path)
    if mbox is None:
        return

    mbox_path_str = str(mbox_path)
    count = 0
    for message in mbox:
        if limit and count >= limit:
            break

        email = _parse_message(message, folder_name, mbox_path_str, include_raw)
        if email:
            yield email
            count += 1

    mbox.close()


def read_mbox_random(
    mbox_path: Path,
    folder_name: str,
    limit: int | float,
    include_raw: bool = False,
) -> Iterator[ThunderbirdEmail]:
    """Read a random sample of emails from an mbox file.

    Args:
        mbox_path: Path to the mbox file
        folder_name: Name to assign to the folder
        limit: Number of emails (int >= 1) or fraction to sample (float 0-1)
        include_raw: If True, capture raw bytes for cross-server transfers

    Yields:
        ThunderbirdEmail objects for each successfully parsed email
    """
    import random

    mbox = _open_mbox(mbox_path)
    if mbox is None:
        return

    try:
        keys = list(mbox.keys())
        total = len(keys)

        if total == 0:
            logger.info(f"No emails in {folder_name}")
            return

        # Calculate sample size: if < 1, treat as percentage; otherwise as count
        if isinstance(limit, float) and limit < 1:
            sample_size = max(1, int(total * limit))
            logger.info(f"Randomly sampling {limit:.0%} ({sample_size} of {total}) emails from {folder_name}")
        else:
            sample_size = min(int(limit), total)
            logger.info(f"Randomly sampling {sample_size} of {total} emails from {folder_name}")

        sampled_keys = random.sample(keys, sample_size)
        mbox_path_str = str(mbox_path)
        yielded = 0

        for key in sampled_keys:
            email = _parse_message(mbox[key], folder_name, mbox_path_str, include_raw)
            if email:
                yield email
                yielded += 1

        logger.info(f"Successfully read {yielded} emails from {folder_name}")
    finally:
        mbox.close()


def get_raw_email(mbox_path: str, message_id: str) -> bytes | None:
    """Retrieve the raw email content from an mbox file by message_id.

    Args:
        mbox_path: Path to the mbox file (must be within ImapMail or Mail directory)
        message_id: Message-ID header to search for

    Returns:
        Raw email bytes if found, None otherwise
    """
    # Validate path is within expected Thunderbird directories
    path = Path(mbox_path).resolve()
    path_str = str(path)
    if "/ImapMail/" not in path_str and "/Mail/" not in path_str:
        logger.error(f"Rejected mbox path outside Thunderbird directories: {mbox_path}")
        return None

    try:
        mbox = mailbox.mbox(mbox_path)
    except Exception as e:
        logger.error(f"Failed to open mbox {mbox_path}: {e}")
        return None

    try:
        for message in mbox:
            if message.get("Message-ID") == message_id:
                return message.as_bytes()
    except Exception as e:
        logger.error(f"Error reading mbox {mbox_path}: {e}")
    finally:
        mbox.close()

    return None
