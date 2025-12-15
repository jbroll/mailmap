"""Classify command - bulk email classification."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..categories import get_category_descriptions, load_categories
from ..config import Config
from ..database import Database, Email
from ..email import UnifiedEmail
from ..llm import OllamaClient
from ..mbox import get_raw_email
from ..spam import is_spam, parse_rules
from ..targets.base import EmailTarget

logger = logging.getLogger("mailmap")


@dataclass
class ProcessingStats:
    """Thread-safe stats for concurrent processing."""

    imported: int = 0
    classified: int = 0
    copied: int = 0
    failed: int = 0
    spam: int = 0
    consecutive_failures: int = 0
    stop_requested: bool = False
    max_consecutive_failures: int = 5
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def increment(self, **kwargs: int) -> None:
        async with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, getattr(self, key) + value)

    async def record_upload_result(self, success: bool) -> bool:
        """Record upload success/failure and check if we should stop.

        Args:
            success: Whether the upload succeeded

        Returns:
            True if processing should stop due to too many consecutive failures
        """
        async with self._lock:
            if success:
                self.copied += 1
                self.consecutive_failures = 0
            else:
                self.failed += 1
                self.consecutive_failures += 1
                if self.consecutive_failures >= self.max_consecutive_failures:
                    self.stop_requested = True
                    return True
            return False

    async def should_stop(self) -> bool:
        """Check if processing should stop."""
        async with self._lock:
            return self.stop_requested


async def _get_raw_bytes(email: UnifiedEmail) -> bytes | None:
    """Get raw email bytes for cross-server transfers.

    Args:
        email: UnifiedEmail with source information

    Returns:
        Raw email bytes if available, None otherwise
    """
    # If raw_bytes already populated, use it
    if email.raw_bytes:
        return email.raw_bytes

    # For Thunderbird source, load from mbox file
    if email.source_type == "thunderbird" and email.source_ref:
        start = time.time()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            get_raw_email,
            str(email.source_ref),
            email.message_id,
        )
        elapsed = time.time() - start
        if elapsed > 1.0:
            logger.debug(f"  mbox read took {elapsed:.1f}s for {email.message_id[:30]}...")
        return result

    # For other sources, raw_bytes must be pre-populated or we return None
    # (IMAP target will try to find email on server)
    return None


async def _transfer_single_email(
    email_record: Email,
    target: EmailTarget,
    db: Database,
    move: bool,
    stats: ProcessingStats,
    rate_limit: float = 1.0,
    track_consecutive: bool = True,
) -> bool:
    """Transfer a pre-classified email to its destination folder with rate limiting.

    Args:
        email_record: Email record from database with classification
        target: Target to transfer to
        db: Database instance
        move: If True, move instead of copy
        stats: Stats tracker
        rate_limit: Minimum seconds between transfers (default: 1.0)
        track_consecutive: If True, use shared consecutive failure tracking

    Returns:
        True if transfer succeeded, False otherwise.
    """
    action_past = "moved" if move else "copied"
    start_time = time.time()

    try:
        target_folder = email_record.classification or "Unknown"

        # For pre-classified emails, we don't have raw bytes readily available
        # The target will need to find the email on the server
        if move:
            success = await target.move_email(email_record.message_id, target_folder, None)
        else:
            success = await target.copy_email(email_record.message_id, target_folder, None)

        elapsed = time.time() - start_time

        if track_consecutive:
            should_stop = await stats.record_upload_result(success)
        else:
            if success:
                await stats.increment(copied=1)
            else:
                await stats.increment(failed=1)
            should_stop = False

        if success:
            db.mark_as_transferred(email_record.message_id)
            logger.info(
                f"  {action_past}: {email_record.subject[:40]}... -> {target_folder} [{elapsed:.1f}s]"
            )
        else:
            logger.warning(f"  Failed to {action_past}: {email_record.message_id}")
            if should_stop:
                logger.error(
                    f"Stopping after {stats.max_consecutive_failures} consecutive upload failures"
                )

        # Rate limiting - ensure minimum time between operations
        if elapsed < rate_limit:
            await asyncio.sleep(rate_limit - elapsed)

        return success

    except Exception as e:
        if track_consecutive:
            await stats.record_upload_result(False)
        else:
            await stats.increment(failed=1)
        logger.warning(f"Failed to transfer {email_record.message_id}: {e}")
        return False


async def _process_single_email(
    email: UnifiedEmail,
    folder_name: str,
    llm: OllamaClient,
    db: Database,
    target: EmailTarget | None,
    folder_descriptions: dict[str, str],
    min_confidence: float,
    move: bool,
    stats: ProcessingStats,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str] | None:
    """Process a single email with semaphore-limited concurrency.

    Returns:
        (message_id, classification) tuple if successful, None otherwise.
    """
    async with semaphore:
        # Check if we should stop due to previous failures
        if await stats.should_stop():
            return None

        action_past = "moved" if move else "copied"
        total_start = time.time()

        try:
            # Classify email
            llm_start = time.time()
            result = await llm.classify_email(
                email.subject,
                email.from_addr,
                email.body_text,
                folder_descriptions,
                attachments=email.attachments,
            )
            llm_elapsed = time.time() - llm_start

            db.update_classification(
                email.message_id,
                result.predicted_folder,
                result.confidence,
            )
            await stats.increment(classified=1)

            # Copy/move if target available
            if target:
                # Check again before upload (may have changed during LLM call)
                if await stats.should_stop():
                    return (email.message_id, result.predicted_folder)

                target_folder = (
                    result.predicted_folder
                    if result.confidence >= min_confidence
                    else "Unknown"
                )

                # Get raw bytes for cross-server transfers
                raw_start = time.time()
                raw_bytes = await _get_raw_bytes(email)
                raw_elapsed = time.time() - raw_start

                upload_start = time.time()
                if move:
                    success = await target.move_email(email.message_id, target_folder, raw_bytes)
                else:
                    success = await target.copy_email(email.message_id, target_folder, raw_bytes)
                upload_elapsed = time.time() - upload_start

                total_elapsed = time.time() - total_start
                timing_info = f"[llm:{llm_elapsed:.1f}s raw:{raw_elapsed:.1f}s upload:{upload_elapsed:.1f}s total:{total_elapsed:.1f}s]"

                # Record result and check for consecutive failures
                should_stop = await stats.record_upload_result(success)

                if success:
                    db.mark_as_transferred(email.message_id)
                    conf_str = (
                        f" ({result.confidence:.0%})"
                        if target_folder != "Unknown"
                        else f" (low: {result.confidence:.0%})"
                    )
                    logger.info(
                        f"  {action_past}: {email.subject[:40]}... -> {target_folder}{conf_str} {timing_info}"
                    )
                else:
                    logger.warning(f"  Failed to {action_past}: {email.message_id} {timing_info}")
                    if should_stop:
                        logger.error(
                            f"Stopping after {stats.max_consecutive_failures} consecutive upload failures"
                        )
            else:
                total_elapsed = time.time() - total_start
                logger.debug(f"  classified: {email.subject[:40]}... -> {result.predicted_folder} [llm:{llm_elapsed:.1f}s total:{total_elapsed:.1f}s]")

            return (email.message_id, result.predicted_folder)

        except Exception as e:
            logger.warning(f"Failed to classify {email.message_id}: {e}")
            return None


async def bulk_classify(
    config: Config,
    db: Database,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
    websocket_port: int | None = None,
    min_confidence: float = 0.5,
    force: bool = False,
    concurrency: int = 1,
    rate_limit: float = 1.0,
) -> list[tuple[str, str]]:
    """Bulk classify emails using source/target abstractions.

    Automatically selects the best email source based on configuration.
    If copy/move is requested, uses the appropriate target.

    Also handles already-classified-but-untransferred emails by transferring
    them with rate limiting (no re-classification needed).

    Args:
        config: Application configuration
        db: Database instance
        copy: If True with target, copy messages to target folders
        move: If True with target, move messages to target folders
        target_account: Target account for folders: 'local', 'imap', or account ID
        websocket_port: Port for WebSocket connection (required for 'local' target)
        min_confidence: Minimum confidence to copy/move (below this goes to Unknown)
        force: If True, re-classify emails even if already in database
        concurrency: Number of emails to process concurrently (default: 1)
        rate_limit: Minimum seconds between transfer-only operations (default: 1.0)

    Returns:
        List of (message_id, classification) tuples for successfully classified emails.
    """
    from ..sources import select_source
    from ..targets import select_target

    classifications: list[tuple[str, str]] = []
    tb_config = config.thunderbird

    # Load categories
    categories_path = Path(config.database.categories_file)
    categories = load_categories(categories_path)
    folder_descriptions = get_category_descriptions(categories)

    if not folder_descriptions:
        logger.error(f"No categories found in {categories_path}")
        logger.error("Run 'mailmap learn' first to generate categories")
        return classifications

    logger.info(f"Loaded {len(folder_descriptions)} categories from {categories_path}")

    # Select source
    try:
        source = select_source(config, config.thunderbird.source_type)
        logger.info(f"Using {source.source_type} source")
    except ValueError as e:
        logger.error(str(e))
        return classifications

    # Select target if copy/move requested
    target = None
    if copy or move:
        try:
            target = select_target(config, target_account, websocket_port)
            logger.info(f"Using {target.target_type} target (account: {target_account})")
        except ValueError as e:
            logger.error(str(e))
            return classifications

    # Load spam rules
    spam_rules = parse_rules(config.spam.rules) if config.spam.enabled else []

    stats = ProcessingStats()
    start_time = time.time()
    semaphore = asyncio.Semaphore(concurrency)

    if concurrency > 1:
        logger.info(f"Using {concurrency} concurrent workers")

    try:
        async with source, OllamaClient(config.ollama) as llm:
            # Connect target if available
            if target:
                await target.connect()

            try:
                # Get folders to process
                all_folders = await source.list_folders()

                if tb_config.folder_filter:
                    # Filter to specific folder (handle server:folder syntax)
                    filter_folder = tb_config.folder_filter
                    matching = [f for f in all_folders if f == filter_folder or f.endswith(f":{filter_folder}")]
                    if not matching:
                        logger.error(f"Folder '{filter_folder}' not found")
                        return classifications
                    if len(matching) > 1:
                        logger.error(
                            f"Folder '{filter_folder}' found in multiple accounts: {matching}. "
                            f"Use server:folder syntax."
                        )
                        return classifications
                    folders = matching
                else:
                    folders = all_folders

                for folder_spec in folders:
                    # Extract folder name for display and skip checks
                    if ":" in folder_spec:
                        _, folder_name = folder_spec.split(":", 1)
                    else:
                        folder_name = folder_spec

                    # Skip spam folders
                    if config.spam.enabled and folder_name in config.spam.skip_folders:
                        logger.info(f"Skipping spam folder: {folder_spec}")
                        continue

                    logger.info(f"Processing folder: {folder_spec}")

                    # Read emails from source
                    limit = int(tb_config.import_limit) if isinstance(tb_config.import_limit, (int, float)) else None
                    random_sample = tb_config.random_sample

                    # Collect emails to process
                    emails_to_classify: list[tuple[UnifiedEmail, str]] = []
                    emails_to_transfer: list[Email] = []  # Already classified, need transfer only

                    async for email in source.read_emails(folder_spec, limit, random_sample):
                        existing = db.get_email(email.message_id)

                        if existing and existing.classification and not force:
                            # Already classified - check if needs transfer
                            if target and not existing.transferred_at:
                                emails_to_transfer.append(existing)
                            continue

                        # Check for spam (if headers available)
                        is_spam_result, spam_reason = False, None
                        if spam_rules and email.headers:
                            is_spam_result, spam_reason = is_spam(email.headers, spam_rules)

                        if is_spam_result:
                            email_record = Email(
                                message_id=email.message_id,
                                folder_id=folder_name,
                                subject=email.subject,
                                from_addr=email.from_addr,
                                mbox_path=str(email.source_ref) if email.source_ref else "",
                                is_spam=True,
                                spam_reason=spam_reason,
                                processed_at=datetime.now(),
                            )
                            db.insert_email(email_record)
                            stats.spam += 1
                            continue

                        # Import email to database
                        email_record = Email(
                            message_id=email.message_id,
                            folder_id=folder_name,
                            subject=email.subject,
                            from_addr=email.from_addr,
                            mbox_path=str(email.source_ref) if email.source_ref else "",
                            processed_at=datetime.now(),
                        )
                        db.insert_email(email_record)
                        stats.imported += 1
                        emails_to_classify.append((email, folder_name))

                    if not emails_to_classify and not emails_to_transfer:
                        logger.info(f"  No emails to process in {folder_name}")
                        continue

                    # Process emails needing classification
                    if emails_to_classify:
                        logger.info(f"  Classifying {len(emails_to_classify)} emails...")

                        # Process emails concurrently
                        tasks = [
                            _process_single_email(
                                email=email,
                                folder_name=fname,
                                llm=llm,
                                db=db,
                                target=target,
                                folder_descriptions=folder_descriptions,
                                min_confidence=min_confidence,
                                move=move,
                                stats=stats,
                                semaphore=semaphore,
                            )
                            for email, fname in emails_to_classify
                        ]

                        # Run with progress reporting
                        results = await asyncio.gather(*tasks)

                        # Collect successful classifications
                        for result in results:
                            if result:
                                classifications.append(result)

                        logger.info(f"  Classified {len(emails_to_classify)} emails from {folder_name}")

                        # Check if we should stop due to consecutive failures
                        if await stats.should_stop():
                            logger.error(
                                f"Exiting after {stats.max_consecutive_failures} consecutive upload failures"
                            )
                            break

                    # Process pre-classified emails needing transfer (rate-limited)
                    if target and emails_to_transfer:
                        # Check if we should skip due to previous failures
                        if await stats.should_stop():
                            break

                        logger.info(f"  Transferring {len(emails_to_transfer)} pre-classified emails (rate: {rate_limit:.1f}s)...")

                        for i, email_record in enumerate(emails_to_transfer, 1):
                            logger.info(f"  [{i}/{len(emails_to_transfer)}] {email_record.subject[:50]}...")
                            success = await _transfer_single_email(
                                email_record=email_record,
                                target=target,
                                db=db,
                                move=move,
                                stats=stats,
                                rate_limit=rate_limit,
                            )
                            if success:
                                classifications.append((email_record.message_id, email_record.classification or "Unknown"))
                            elif await stats.should_stop():
                                break

                        if await stats.should_stop():
                            break  # Exit folder loop too

                        logger.info(f"  Transferred {len(emails_to_transfer)} emails from {folder_name}")

            finally:
                if target:
                    await target.disconnect()

    except Exception as e:
        logger.error(f"Error during classification: {e}")
        raise

    elapsed = time.time() - start_time
    rate = stats.classified / elapsed if elapsed > 0 else 0
    logger.info(
        f"Classification complete: {stats.imported} imported, {stats.classified} classified, {stats.spam} spam"
    )
    logger.info(f"Elapsed time: {elapsed:.1f}s, rate: {rate:.2f} emails/sec")
    if target:
        logger.info(f"Target actions: {stats.copied} transferred, {stats.failed} failed")

    return classifications


async def run_bulk_classify(
    config: Config,
    db: Database,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
    websocket_port: int | None = None,
    force: bool = False,
    concurrency: int = 1,
    rate_limit: float = 1.0,
) -> None:
    """Run bulk classification mode.

    Args:
        config: Application configuration
        db: Database instance
        copy: If True, copy classified emails to target folders
        move: If True, move classified emails to target folders
        target_account: Target account for folders: 'local', 'imap', or account ID
        websocket_port: If provided, use WebSocket on this port (requires Thunderbird extension)
        force: If True, re-classify emails even if already processed
        concurrency: Number of emails to process concurrently (default: 1)
        rate_limit: Minimum seconds between transfer-only operations (default: 1.0)
    """
    if copy and move:
        logger.error("Cannot specify both --copy and --move")
        return

    # Validate target requirements
    if (copy or move) and target_account == "local" and websocket_port is None:
        logger.error("Target 'local' requires --websocket. Use --target-account imap for direct IMAP.")
        return

    db.connect()
    db.init_schema()

    try:
        await bulk_classify(
            config, db,
            copy=copy, move=move,
            target_account=target_account,
            websocket_port=websocket_port,
            force=force,
            concurrency=concurrency,
            rate_limit=rate_limit,
        )
    finally:
        db.close()


async def transfer_emails(
    config: Config,
    db: Database,
    move: bool = False,
    rate_limit: float = 1.0,
    max_consecutive_failures: int = 5,
) -> int:
    """Transfer pre-classified emails to their destination IMAP folders.

    Processes emails that are classified but not yet transferred,
    with rate limiting to avoid overwhelming the IMAP server.

    Args:
        config: Application configuration
        db: Database instance
        move: If True, move instead of copy
        rate_limit: Minimum seconds between IMAP operations (default: 1.0)
        max_consecutive_failures: Exit after this many consecutive failures (default: 5)

    Returns:
        Number of emails successfully transferred.
    """
    from ..targets import ImapTarget

    db.connect()
    db.init_schema()

    try:
        # Get untransferred emails
        untransferred = db.get_untransferred_emails()
        if not untransferred:
            logger.info("No untransferred emails found")
            return 0

        logger.info(f"Found {len(untransferred)} classified but untransferred emails")
        logger.info(f"Rate limit: {rate_limit:.1f}s between operations")

        target = ImapTarget(config.imap)
        stats = ProcessingStats()
        stats.max_consecutive_failures = max_consecutive_failures
        start_time = time.time()

        async with target:
            for i, email_record in enumerate(untransferred, 1):
                logger.info(f"[{i}/{len(untransferred)}] {email_record.subject[:50]}...")
                await _transfer_single_email(
                    email_record=email_record,
                    target=target,
                    db=db,
                    move=move,
                    stats=stats,
                    rate_limit=rate_limit,
                )

                if await stats.should_stop():
                    break

        elapsed = time.time() - start_time
        logger.info(f"Transfer complete: {stats.copied} transferred, {stats.failed} failed")
        logger.info(f"Elapsed time: {elapsed:.1f}s")

        return stats.copied

    finally:
        db.close()
