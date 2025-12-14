"""SQLite database operations for mailmap."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Email:
    """Email record for classification tracking.

    Note: We store mbox_path instead of body_text to save space.
    The original email can be retrieved from the mbox file by message_id.
    """
    message_id: str
    folder_id: str  # Original folder the email came from
    subject: str
    from_addr: str
    mbox_path: str  # Path to mbox file for retrieving original email
    classification: str | None = None
    confidence: float | None = None
    is_spam: bool = False
    spam_reason: str | None = None  # Which rule matched
    processed_at: datetime | None = None
    transferred_at: datetime | None = None  # When email was copied/moved to target


SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    folder_id TEXT NOT NULL,
    subject TEXT,
    from_addr TEXT,
    mbox_path TEXT,
    classification TEXT,
    confidence REAL,
    is_spam INTEGER DEFAULT 0,
    spam_reason TEXT,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder_id);
CREATE INDEX IF NOT EXISTS idx_emails_classification ON emails(classification);
CREATE INDEX IF NOT EXISTS idx_emails_is_spam ON emails(is_spam);
"""


class Database:
    """SQLite database wrapper with connection management.

    Can be used as a context manager for automatic connection handling:

        with Database(path) as db:
            emails = db.get_unclassified_emails()
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "Database":
        """Connect to database and initialize schema."""
        self.connect()
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close database connection."""
        self.close()

    def connect(self) -> None:
        """Open database connection."""
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get the active database connection.

        Raises:
            RuntimeError: If database is not connected
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    def init_schema(self) -> None:
        """Initialize database schema, including migrations."""
        self.conn.executescript(SCHEMA)

        # Migration: add columns if they don't exist
        cursor = self.conn.execute("PRAGMA table_info(emails)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "is_spam" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN is_spam INTEGER DEFAULT 0")
        if "spam_reason" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN spam_reason TEXT")
        if "transferred_at" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN transferred_at TIMESTAMP")

        self.conn.commit()

    def insert_email(self, email: Email) -> None:
        """Insert or replace an email record."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO emails
            (message_id, folder_id, subject, from_addr, mbox_path,
             classification, confidence, is_spam, spam_reason, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.message_id,
                email.folder_id,
                email.subject,
                email.from_addr,
                email.mbox_path,
                email.classification,
                email.confidence,
                1 if email.is_spam else 0,
                email.spam_reason,
                email.processed_at,
            ),
        )
        self.conn.commit()

    def get_email(self, message_id: str) -> Email | None:
        """Get an email by message ID."""
        row = self.conn.execute(
            "SELECT * FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row:
            return self._row_to_email(row)
        return None

    def _row_to_email(self, row: sqlite3.Row) -> Email:
        """Convert a database row to an Email object."""
        # Handle transferred_at which may not exist in older databases
        transferred_at = None
        if "transferred_at" in row.keys():  # noqa: SIM118
            transferred_at = row["transferred_at"]

        return Email(
            message_id=row["message_id"],
            folder_id=row["folder_id"],
            subject=row["subject"],
            from_addr=row["from_addr"],
            mbox_path=row["mbox_path"],
            classification=row["classification"],
            confidence=row["confidence"],
            is_spam=bool(row["is_spam"]) if row["is_spam"] is not None else False,
            spam_reason=row["spam_reason"],
            processed_at=row["processed_at"],
            transferred_at=transferred_at,
        )

    def update_classification(
        self, message_id: str, classification: str, confidence: float
    ) -> None:
        """Update the classification for an email."""
        self.conn.execute(
            """
            UPDATE emails
            SET classification = ?, confidence = ?, processed_at = ?
            WHERE message_id = ?
            """,
            (classification, confidence, datetime.now(), message_id),
        )
        self.conn.commit()

    def mark_as_spam(self, message_id: str, reason: str) -> None:
        """Mark an email as spam with the matching rule."""
        self.conn.execute(
            """
            UPDATE emails
            SET is_spam = 1, spam_reason = ?, classification = 'Spam'
            WHERE message_id = ?
            """,
            (reason, message_id),
        )
        self.conn.commit()

    def mark_as_transferred(self, message_id: str) -> None:
        """Mark an email as successfully transferred to target folder."""
        self.conn.execute(
            """
            UPDATE emails
            SET transferred_at = ?
            WHERE message_id = ?
            """,
            (datetime.now(), message_id),
        )
        self.conn.commit()

    def clear_all_transfers(self) -> int:
        """Clear transferred_at on all emails.

        Returns:
            Number of emails affected
        """
        cursor = self.conn.execute(
            "UPDATE emails SET transferred_at = NULL WHERE transferred_at IS NOT NULL"
        )
        self.conn.commit()
        return cursor.rowcount

    def mark_many_as_transferred(self, message_ids: list[str]) -> int:
        """Mark multiple emails as transferred in a single transaction.

        Args:
            message_ids: List of message IDs to mark as transferred

        Returns:
            Number of emails updated
        """
        if not message_ids:
            return 0

        now = datetime.now()
        # Use executemany for efficiency
        self.conn.executemany(
            "UPDATE emails SET transferred_at = ? WHERE message_id = ?",
            [(now, msg_id) for msg_id in message_ids],
        )
        self.conn.commit()
        return len(message_ids)

    def get_emails_by_classification(self, classification: str) -> list[Email]:
        """Get all emails with a specific classification (for upload)."""
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE classification = ?",
            (classification,),
        ).fetchall()
        return [self._row_to_email(row) for row in rows]

    def get_classification_counts(self) -> dict[str, int]:
        """Get count of emails per classification."""
        rows = self.conn.execute(
            """
            SELECT classification, COUNT(*) as count
            FROM emails
            WHERE classification IS NOT NULL
            GROUP BY classification
            ORDER BY count DESC
            """
        ).fetchall()
        return {row["classification"]: row["count"] for row in rows}

    def get_unclassified_emails(self, include_spam: bool = False) -> list[Email]:
        """Get emails that haven't been classified yet.

        Args:
            include_spam: If False (default), exclude emails marked as spam
        """
        if include_spam:
            query = "SELECT * FROM emails WHERE classification IS NULL"
        else:
            query = "SELECT * FROM emails WHERE classification IS NULL AND is_spam = 0"

        rows = self.conn.execute(query).fetchall()
        return [self._row_to_email(row) for row in rows]

    def clear_classifications(self, folder_id: str | None = None) -> int:
        """Clear classifications from emails.

        Args:
            folder_id: If provided, only clear emails from this folder.
                      If None, clear all classifications.

        Returns:
            Number of emails affected
        """
        if folder_id:
            cursor = self.conn.execute(
                """
                UPDATE emails
                SET classification = NULL, confidence = NULL
                WHERE folder_id = ? AND is_spam = 0
                """,
                (folder_id,),
            )
        else:
            cursor = self.conn.execute(
                """
                UPDATE emails
                SET classification = NULL, confidence = NULL
                WHERE is_spam = 0
                """
            )
        self.conn.commit()
        return cursor.rowcount

    def get_spam_count(self) -> int:
        """Get count of emails marked as spam."""
        row = self.conn.execute(
            "SELECT COUNT(*) as count FROM emails WHERE is_spam = 1"
        ).fetchone()
        return row["count"] if row else 0

    def get_total_count(self) -> int:
        """Get total count of emails."""
        row = self.conn.execute("SELECT COUNT(*) as count FROM emails").fetchone()
        return row["count"] if row else 0

    def get_classified_count(self) -> int:
        """Get count of classified emails (excluding spam)."""
        row = self.conn.execute(
            "SELECT COUNT(*) as count FROM emails WHERE classification IS NOT NULL AND is_spam = 0"
        ).fetchone()
        return row["count"] if row else 0

    def get_transferred_count(self) -> int:
        """Get count of emails successfully transferred to target folder."""
        row = self.conn.execute(
            "SELECT COUNT(*) as count FROM emails WHERE transferred_at IS NOT NULL"
        ).fetchone()
        return row["count"] if row else 0

    def get_untransferred_emails(self) -> list[Email]:
        """Get classified emails that haven't been transferred yet."""
        rows = self.conn.execute(
            """
            SELECT * FROM emails
            WHERE classification IS NOT NULL
            AND transferred_at IS NULL
            AND is_spam = 0
            """
        ).fetchall()
        return [self._row_to_email(row) for row in rows]

    def get_recent_classifications(self, limit: int = 50) -> list[Email]:
        """Get recently classified emails."""
        rows = self.conn.execute(
            """
            SELECT * FROM emails
            WHERE classification IS NOT NULL
            ORDER BY processed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_email(row) for row in rows]

    def get_classification_summary(self) -> list[tuple[str, int]]:
        """Get classification counts grouped by category (excluding spam)."""
        rows = self.conn.execute(
            """
            SELECT classification, COUNT(*) as count
            FROM emails
            WHERE classification IS NOT NULL AND is_spam = 0
            GROUP BY classification
            ORDER BY count DESC
            """
        ).fetchall()
        return [(row["classification"], row["count"]) for row in rows]
