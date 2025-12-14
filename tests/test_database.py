"""Tests for database module."""

from datetime import datetime

import pytest

from mailmap.database import Database, Email


class TestDatabase:
    def test_connect_and_init(self, temp_dir):
        db = Database(temp_dir / "test.db")
        db.connect()
        db.init_schema()
        assert db._conn is not None
        db.close()
        assert db._conn is None

    def test_conn_property_raises_when_not_connected(self, temp_dir):
        db = Database(temp_dir / "test.db")
        with pytest.raises(RuntimeError, match="Database not connected"):
            _ = db.conn

    def test_context_manager(self, temp_dir):
        with Database(temp_dir / "test.db") as db:
            assert db._conn is not None
        assert db._conn is None


class TestEmailOperations:
    def test_insert_and_get_email(self, test_db):
        email = Email(
            message_id="<test123@example.com>",
            folder_id="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            mbox_path="/path/to/mbox",
            processed_at=datetime.now(),
        )
        test_db.insert_email(email)

        retrieved = test_db.get_email("<test123@example.com>")
        assert retrieved is not None
        assert retrieved.message_id == "<test123@example.com>"
        assert retrieved.subject == "Test Subject"
        assert retrieved.from_addr == "sender@example.com"
        assert retrieved.mbox_path == "/path/to/mbox"

    def test_get_nonexistent_email(self, test_db):
        result = test_db.get_email("<nonexistent@example.com>")
        assert result is None

    def test_update_classification(self, test_db):
        email = Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Test",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email)

        test_db.update_classification("<test@example.com>", "Receipts", 0.95)

        retrieved = test_db.get_email("<test@example.com>")
        assert retrieved.classification == "Receipts"
        assert retrieved.confidence == 0.95
        assert retrieved.processed_at is not None

    def test_get_unclassified_emails(self, test_db):
        # Insert classified email
        email1 = Email(
            message_id="<classified@example.com>",
            folder_id="INBOX",
            subject="Classified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
            confidence=0.9,
        )
        test_db.insert_email(email1)

        # Insert unclassified email
        email2 = Email(
            message_id="<unclassified@example.com>",
            folder_id="INBOX",
            subject="Unclassified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email2)

        unclassified = test_db.get_unclassified_emails()
        assert len(unclassified) == 1
        assert unclassified[0].message_id == "<unclassified@example.com>"

    def test_get_unclassified_excludes_spam(self, test_db):
        # Insert spam email (unclassified but marked as spam)
        spam_email = Email(
            message_id="<spam@example.com>",
            folder_id="INBOX",
            subject="Buy now!",
            from_addr="spammer@test.com",
            mbox_path="/path/to/mbox",
            is_spam=True,
            spam_reason="X-Spam-Flag == YES",
        )
        test_db.insert_email(spam_email)

        # Insert regular unclassified email
        regular_email = Email(
            message_id="<regular@example.com>",
            folder_id="INBOX",
            subject="Regular",
            from_addr="friend@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(regular_email)

        unclassified = test_db.get_unclassified_emails()
        assert len(unclassified) == 1
        assert unclassified[0].message_id == "<regular@example.com>"

        # With include_spam=True
        all_unclassified = test_db.get_unclassified_emails(include_spam=True)
        assert len(all_unclassified) == 2

    def test_mark_as_spam(self, test_db):
        email = Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Spam",
            from_addr="spammer@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email)

        test_db.mark_as_spam("<test@example.com>", "X-Spam-Flag == YES")

        retrieved = test_db.get_email("<test@example.com>")
        assert retrieved.is_spam is True
        assert retrieved.spam_reason == "X-Spam-Flag == YES"
        assert retrieved.classification == "Spam"

    def test_clear_classifications(self, test_db):
        # Insert some classified emails
        for i in range(5):
            email = Email(
                message_id=f"<test{i}@example.com>",
                folder_id="INBOX",
                subject=f"Test {i}",
                from_addr="test@test.com",
                mbox_path="/path/to/mbox",
                classification="Work",
                confidence=0.9,
            )
            test_db.insert_email(email)

        # Clear all
        count = test_db.clear_classifications()
        assert count == 5

        # Verify cleared
        for i in range(5):
            retrieved = test_db.get_email(f"<test{i}@example.com>")
            assert retrieved.classification is None
            assert retrieved.confidence is None

    def test_clear_classifications_by_folder(self, test_db):
        # Insert emails in different folders
        email1 = Email(
            message_id="<inbox@example.com>",
            folder_id="INBOX",
            subject="Inbox",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        email2 = Email(
            message_id="<sent@example.com>",
            folder_id="Sent",
            subject="Sent",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        test_db.insert_email(email1)
        test_db.insert_email(email2)

        # Clear only INBOX
        count = test_db.clear_classifications("INBOX")
        assert count == 1

        # INBOX should be cleared
        retrieved1 = test_db.get_email("<inbox@example.com>")
        assert retrieved1.classification is None

        # Sent should still be classified
        retrieved2 = test_db.get_email("<sent@example.com>")
        assert retrieved2.classification == "Work"

    def test_clear_classifications_preserves_spam(self, test_db):
        # Insert spam email
        spam = Email(
            message_id="<spam@example.com>",
            folder_id="INBOX",
            subject="Spam",
            from_addr="spammer@test.com",
            mbox_path="/path/to/mbox",
            classification="Spam",
            is_spam=True,
        )
        test_db.insert_email(spam)

        # Clear classifications
        test_db.clear_classifications()

        # Spam should still be classified
        retrieved = test_db.get_email("<spam@example.com>")
        assert retrieved.classification == "Spam"
        assert retrieved.is_spam is True

    def test_get_classification_counts(self, test_db):
        # Insert emails with different classifications
        for i in range(3):
            email = Email(
                message_id=f"<work{i}@example.com>",
                folder_id="INBOX",
                subject=f"Work {i}",
                from_addr="test@test.com",
                mbox_path="/path/to/mbox",
                classification="Work",
            )
            test_db.insert_email(email)

        for i in range(2):
            email = Email(
                message_id=f"<personal{i}@example.com>",
                folder_id="INBOX",
                subject=f"Personal {i}",
                from_addr="test@test.com",
                mbox_path="/path/to/mbox",
                classification="Personal",
            )
            test_db.insert_email(email)

        counts = test_db.get_classification_counts()
        assert counts == {"Work": 3, "Personal": 2}

    def test_get_emails_by_classification(self, test_db):
        # Insert emails
        email1 = Email(
            message_id="<work1@example.com>",
            folder_id="INBOX",
            subject="Work 1",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        email2 = Email(
            message_id="<personal1@example.com>",
            folder_id="INBOX",
            subject="Personal 1",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Personal",
        )
        test_db.insert_email(email1)
        test_db.insert_email(email2)

        work_emails = test_db.get_emails_by_classification("Work")
        assert len(work_emails) == 1
        assert work_emails[0].message_id == "<work1@example.com>"

    def test_count_methods(self, test_db):
        # Insert various emails
        email1 = Email(
            message_id="<classified@example.com>",
            folder_id="INBOX",
            subject="Classified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        email2 = Email(
            message_id="<unclassified@example.com>",
            folder_id="INBOX",
            subject="Unclassified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        email3 = Email(
            message_id="<spam@example.com>",
            folder_id="INBOX",
            subject="Spam",
            from_addr="spammer@test.com",
            mbox_path="/path/to/mbox",
            is_spam=True,
            classification="Spam",
        )
        test_db.insert_email(email1)
        test_db.insert_email(email2)
        test_db.insert_email(email3)

        assert test_db.get_total_count() == 3
        assert test_db.get_classified_count() == 1  # Excludes spam
        assert test_db.get_spam_count() == 1

    def test_mark_as_transferred(self, test_db):
        email = Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Test",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
            confidence=0.9,
        )
        test_db.insert_email(email)

        # Initially not transferred
        retrieved = test_db.get_email("<test@example.com>")
        assert retrieved.transferred_at is None

        # Mark as transferred
        test_db.mark_as_transferred("<test@example.com>")

        retrieved = test_db.get_email("<test@example.com>")
        assert retrieved.transferred_at is not None

    def test_get_transferred_count(self, test_db):
        # Insert some emails
        for i in range(3):
            email = Email(
                message_id=f"<test{i}@example.com>",
                folder_id="INBOX",
                subject=f"Test {i}",
                from_addr="test@test.com",
                mbox_path="/path/to/mbox",
                classification="Work",
            )
            test_db.insert_email(email)

        # Initially none transferred
        assert test_db.get_transferred_count() == 0

        # Mark two as transferred
        test_db.mark_as_transferred("<test0@example.com>")
        test_db.mark_as_transferred("<test1@example.com>")

        assert test_db.get_transferred_count() == 2

    def test_get_untransferred_emails(self, test_db):
        # Insert classified emails
        email1 = Email(
            message_id="<transferred@example.com>",
            folder_id="INBOX",
            subject="Transferred",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        email2 = Email(
            message_id="<untransferred@example.com>",
            folder_id="INBOX",
            subject="Untransferred",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Personal",
        )
        email3 = Email(
            message_id="<unclassified@example.com>",
            folder_id="INBOX",
            subject="Unclassified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email1)
        test_db.insert_email(email2)
        test_db.insert_email(email3)

        # Mark one as transferred
        test_db.mark_as_transferred("<transferred@example.com>")

        # Get untransferred - should only return classified but not transferred
        untransferred = test_db.get_untransferred_emails()
        assert len(untransferred) == 1
        assert untransferred[0].message_id == "<untransferred@example.com>"

    def test_get_untransferred_excludes_spam(self, test_db):
        # Insert spam email that's classified but not transferred
        spam_email = Email(
            message_id="<spam@example.com>",
            folder_id="INBOX",
            subject="Spam",
            from_addr="spammer@test.com",
            mbox_path="/path/to/mbox",
            classification="Spam",
            is_spam=True,
        )
        # Insert regular classified but untransferred email
        regular_email = Email(
            message_id="<regular@example.com>",
            folder_id="INBOX",
            subject="Regular",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
        )
        test_db.insert_email(spam_email)
        test_db.insert_email(regular_email)

        # Only regular email should be returned
        untransferred = test_db.get_untransferred_emails()
        assert len(untransferred) == 1
        assert untransferred[0].message_id == "<regular@example.com>"
