"""Unified email representation for source/target abstraction."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mailmap.thunderbird import ThunderbirdEmail


@dataclass
class UnifiedEmail:
    """Unified email representation across all sources.

    This dataclass provides a common interface for emails regardless of
    whether they come from IMAP, Thunderbird cache, or WebSocket.
    """

    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str
    source_type: str = ""  # "imap", "thunderbird", "websocket"
    source_ref: Any = None  # uid (IMAP) or mbox_path (Thunderbird)
    headers: dict[str, str] = field(default_factory=dict)
    raw_bytes: bytes | None = None  # Raw email content for cross-server transfers

    @classmethod
    def from_thunderbird(cls, tb_email: "ThunderbirdEmail") -> "UnifiedEmail":
        """Create UnifiedEmail from ThunderbirdEmail."""
        return cls(
            message_id=tb_email.message_id,
            folder=tb_email.folder,
            subject=tb_email.subject,
            from_addr=tb_email.from_addr,
            body_text=tb_email.body_text,
            source_type="thunderbird",
            source_ref=tb_email.mbox_path,
            headers=tb_email.headers or {},
            raw_bytes=tb_email.raw_bytes,
        )

    @classmethod
    def from_imap(
        cls,
        message_id: str,
        folder: str,
        subject: str,
        from_addr: str,
        body_text: str,
        uid: int,
        headers: dict[str, str] | None = None,
    ) -> "UnifiedEmail":
        """Create UnifiedEmail from IMAP fetch result."""
        return cls(
            message_id=message_id,
            folder=folder,
            subject=subject,
            from_addr=from_addr,
            body_text=body_text,
            source_type="imap",
            source_ref=uid,
            headers=headers or {},
        )

    @classmethod
    def from_websocket(
        cls,
        message_id: str,
        folder: str,
        subject: str,
        from_addr: str,
        body_text: str,
        headers: dict[str, str] | None = None,
    ) -> "UnifiedEmail":
        """Create UnifiedEmail from WebSocket message lookup."""
        return cls(
            message_id=message_id,
            folder=folder,
            subject=subject,
            from_addr=from_addr,
            body_text=body_text,
            source_type="websocket",
            source_ref=None,
            headers=headers or {},
        )
