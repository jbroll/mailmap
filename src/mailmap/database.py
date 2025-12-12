"""SQLite database operations for mailmap."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Folder:
    folder_id: str
    name: str
    description: str | None = None
    last_updated: datetime | None = None


@dataclass
class Email:
    message_id: str
    folder_id: str
    subject: str
    from_addr: str
    body_text: str
    classification: str | None = None
    confidence: float | None = None
    processed_at: datetime | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    folder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    folder_id TEXT NOT NULL,
    subject TEXT,
    from_addr TEXT,
    body_text TEXT,
    classification TEXT,
    confidence REAL,
    processed_at TIMESTAMP,
    FOREIGN KEY (folder_id) REFERENCES folders(folder_id)
);

CREATE TABLE IF NOT EXISTS folder_email_map (
    message_id TEXT,
    folder_id TEXT,
    classification_version INTEGER DEFAULT 1,
    PRIMARY KEY (message_id, folder_id),
    FOREIGN KEY (message_id) REFERENCES emails(message_id),
    FOREIGN KEY (folder_id) REFERENCES folders(folder_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder_id);
CREATE INDEX IF NOT EXISTS idx_emails_classification ON emails(classification);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_folder(self, folder: Folder) -> None:
        self.conn.execute(
            """
            INSERT INTO folders (folder_id, name, description, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(folder_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                last_updated = excluded.last_updated
            """,
            (folder.folder_id, folder.name, folder.description, folder.last_updated),
        )
        self.conn.commit()

    def get_folder(self, folder_id: str) -> Folder | None:
        row = self.conn.execute(
            "SELECT * FROM folders WHERE folder_id = ?", (folder_id,)
        ).fetchone()
        if row:
            return Folder(
                folder_id=row["folder_id"],
                name=row["name"],
                description=row["description"],
                last_updated=row["last_updated"],
            )
        return None

    def get_all_folders(self) -> list[Folder]:
        rows = self.conn.execute("SELECT * FROM folders").fetchall()
        return [
            Folder(
                folder_id=row["folder_id"],
                name=row["name"],
                description=row["description"],
                last_updated=row["last_updated"],
            )
            for row in rows
        ]

    def get_folder_descriptions(self) -> dict[str, str]:
        """Return a mapping of folder_id -> description for all folders with descriptions."""
        rows = self.conn.execute(
            "SELECT folder_id, description FROM folders WHERE description IS NOT NULL"
        ).fetchall()
        return {row["folder_id"]: row["description"] for row in rows}

    def insert_email(self, email: Email) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO emails
            (message_id, folder_id, subject, from_addr, body_text, classification, confidence, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.message_id,
                email.folder_id,
                email.subject,
                email.from_addr,
                email.body_text,
                email.classification,
                email.confidence,
                email.processed_at,
            ),
        )
        self.conn.commit()

    def get_email(self, message_id: str) -> Email | None:
        row = self.conn.execute(
            "SELECT * FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row:
            return Email(
                message_id=row["message_id"],
                folder_id=row["folder_id"],
                subject=row["subject"],
                from_addr=row["from_addr"],
                body_text=row["body_text"],
                classification=row["classification"],
                confidence=row["confidence"],
                processed_at=row["processed_at"],
            )
        return None

    def update_classification(
        self, message_id: str, classification: str, confidence: float
    ) -> None:
        self.conn.execute(
            """
            UPDATE emails
            SET classification = ?, confidence = ?, processed_at = ?
            WHERE message_id = ?
            """,
            (classification, confidence, datetime.now(), message_id),
        )
        self.conn.commit()

    def get_sample_emails(self, folder_id: str, limit: int = 10) -> list[Email]:
        """Get sample emails from a folder for description generation."""
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE folder_id = ? ORDER BY processed_at DESC LIMIT ?",
            (folder_id, limit),
        ).fetchall()
        return [
            Email(
                message_id=row["message_id"],
                folder_id=row["folder_id"],
                subject=row["subject"],
                from_addr=row["from_addr"],
                body_text=row["body_text"],
                classification=row["classification"],
                confidence=row["confidence"],
                processed_at=row["processed_at"],
            )
            for row in rows
        ]

    def get_unclassified_emails(self) -> list[Email]:
        """Get emails that haven't been classified yet."""
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE classification IS NULL"
        ).fetchall()
        return [
            Email(
                message_id=row["message_id"],
                folder_id=row["folder_id"],
                subject=row["subject"],
                from_addr=row["from_addr"],
                body_text=row["body_text"],
                classification=row["classification"],
                confidence=row["confidence"],
                processed_at=row["processed_at"],
            )
            for row in rows
        ]
