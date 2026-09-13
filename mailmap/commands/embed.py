"""Embed command — bootstrap embeddings and folder centroids."""

from __future__ import annotations

import logging
from datetime import datetime

from ..config import Config
from ..database import Database, Email
from ..embedding import EmbeddingClient, centroid, vec_from_bytes, vec_to_bytes

logger = logging.getLogger("mailmap")

BATCH_SIZE = 32  # emails per Ollama embed request

# IMAP folders that are never useful as classification targets.
SYSTEM_FOLDERS = {
    "INBOX",
    "Drafts", "Draft",
    "Sent", "Sent Items", "Sent Messages",
    "Trash", "Deleted", "Deleted Items",
    "Junk", "Spam", "Junk Email",
    "Archive", "Archives",
}


async def run_embed_command(
    config: Config,
    db: Database,
    *,
    rebuild: bool = False,
    seed_from_imap: bool = False,
) -> None:
    """Compute missing embeddings for classified emails and rebuild centroids.

    Args:
        config: Application configuration
        db: Connected and initialized database
        rebuild: Wipe all embeddings and centroids and recompute from scratch.
        seed_from_imap: Scan ALL IMAP folders, import emails not yet in DB
                        with classification=folder_name, then embed everything.
    """
    if rebuild:
        logger.info("--rebuild: clearing all embeddings and centroids...")
        db.conn.execute("UPDATE emails SET embedding = NULL")
        db.conn.execute("DELETE FROM folder_centroids")
        db.conn.commit()
        logger.info("Cleared.")

    if seed_from_imap:
        await _seed_from_imap(config, db)

    emails = db.get_emails_missing_embeddings()
    logger.info(f"{len(emails)} email(s) need embeddings")

    if emails:
        async with EmbeddingClient(config.ollama) as embedder:
            await _embed_emails(db, embedder, emails)

    # Rebuild all centroids from current classifications + embeddings.
    await _rebuild_all_centroids(config, db)


async def _seed_from_imap(config: Config, db: Database) -> None:
    """Scan every non-system IMAP folder and import emails into DB.

    Each email is stored with classification = the IMAP folder name it lives in.
    Emails already in the DB are skipped (no overwrite of existing classifications).
    Folders not in categories.txt are still imported — their centroids will be
    available as training signal even if the classifier doesn't route to them.
    """
    from ..imap_client import ImapClient

    client = ImapClient(config.imap)
    client.connect()
    logger.info(f"Connected to {config.imap.host}")

    try:
        all_folders = client.list_folders()
        user_folders = [f for f in all_folders if f not in SYSTEM_FOLDERS]
        logger.info(
            f"Found {len(all_folders)} folders, "
            f"scanning {len(user_folders)} non-system folders"
        )

        total_imported = 0
        total_skipped = 0

        for folder in sorted(user_folders):
            try:
                headers = client.fetch_all_headers(folder)
            except Exception as e:
                logger.warning(f"  {folder}: fetch failed — {e}")
                continue

            if not headers:
                logger.debug(f"  {folder}: empty")
                continue

            imported = 0
            for _uid, msg_id, from_addr, subject in headers:
                # Don't overwrite an existing DB entry — user may have manually
                # corrected its classification already.
                if db.get_email(msg_id) is not None:
                    total_skipped += 1
                    continue

                email_record = Email(
                    message_id=msg_id,
                    folder_id=folder,
                    subject=subject,
                    from_addr=from_addr,
                    mbox_path="",
                    classification=folder,
                    confidence=1.0,
                    processed_at=datetime.now(),
                )
                db.insert_email(email_record)
                # update_classification sets processed_at — use insert directly
                # but we need to set classification on insert.  insert_email uses
                # INSERT OR REPLACE which picks up the classification field.
                imported += 1

            logger.info(f"  {folder}: {imported} imported, {len(headers) - imported} already in DB")
            total_imported += imported

        logger.info(f"Seed complete: {total_imported} new emails imported")

    finally:
        client.disconnect()


async def _embed_emails(db: Database, embedder: EmbeddingClient, emails) -> None:
    """Compute and store embeddings for a list of Email objects in batches."""
    from ..content import extract_email_summary

    total = len(emails)
    done = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = emails[batch_start: batch_start + BATCH_SIZE]

        texts: list[str] = []
        for email in batch:
            cleaned = extract_email_summary(
                email.subject or "",
                email.from_addr or "",
                "",
                max_body_length=0,
            )
            texts.append(
                f"Subject: {cleaned['subject']}\nFrom: {cleaned['from_addr']}"
            )

        try:
            blobs = await embedder.embed_batch(texts)
        except Exception as e:
            logger.warning(f"Batch embedding failed: {e} — skipping batch")
            continue

        for email, blob in zip(batch, blobs, strict=True):
            db.set_embedding(email.message_id, blob)

        done += len(batch)
        logger.info(f"Embedded {done}/{total}...")

    logger.info(f"Embedding complete: {done} emails processed")


async def _rebuild_all_centroids(config: Config, db: Database) -> None:
    """Recompute centroids for every classification that has enough embeddings."""
    min_ex = config.embedding.min_examples
    classifications = db.get_all_classifications_with_embeddings()
    logger.info(f"Rebuilding centroids for {len(classifications)} classification(s)...")

    updated = 0
    skipped = 0

    for cls in sorted(classifications):
        rows = db.get_embeddings_by_classification(cls)
        if len(rows) < min_ex:
            db.delete_centroid(cls)
            logger.debug(f"  {cls}: {len(rows)} < {min_ex} — skipped")
            skipped += 1
            continue
        vecs = [vec_from_bytes(blob) for _, blob in rows]
        c = centroid(vecs)
        db.upsert_centroid(cls, vec_to_bytes(c), len(vecs))
        logger.info(f"  {cls}: centroid updated ({len(vecs)} embeddings)")
        updated += 1

    logger.info(f"Centroids: {updated} updated, {skipped} skipped (< {min_ex} embeddings)")
