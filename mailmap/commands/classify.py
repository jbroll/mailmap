"""Classify command - bulk email classification."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..categories import get_category_descriptions, load_categories
from ..config import Config
from ..database import Database, Email
from ..llm import OllamaClient
from ..spam import is_spam, parse_rules

if TYPE_CHECKING:
    from ..websocket_server import WebSocketServer

logger = logging.getLogger("mailmap")


async def bulk_classify(
    config: Config,
    db: Database,
    ws_server: WebSocketServer | None = None,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
    min_confidence: float = 0.5,
) -> list[tuple[str, str]]:
    """Bulk classify emails using source/target abstractions.

    Automatically selects the best email source based on configuration.
    If copy/move is requested, uses the appropriate target.

    Args:
        config: Application configuration
        db: Database instance
        ws_server: Optional WebSocket server for copy/move operations
        copy: If True with target, copy messages to target folders
        move: If True with target, move messages to target folders
        target_account: Target account for folders: 'local', 'imap', or account ID
        min_confidence: Minimum confidence to copy/move (below this goes to Unknown)

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
    if ws_server or ((copy or move) and target_account == "imap"):
        try:
            target = select_target(config, ws_server, target_account)
            logger.info(f"Using {target.target_type} target (account: {target_account})")
        except ValueError as e:
            logger.error(str(e))
            return classifications

    # Error if copy/move requested but no target available
    if (copy or move) and target is None:
        logger.error(
            f"No target available for {'move' if move else 'copy'}. "
            f"Use --target-account imap or --websocket."
        )
        return classifications

    # Load spam rules
    spam_rules = parse_rules(config.spam.rules) if config.spam.enabled else []

    total_imported = 0
    total_classified = 0
    total_copied = 0
    total_failed = 0
    total_spam = 0

    action_verb = "Moving" if move else "Copying"
    action_past = "moved" if move else "copied"
    start_time = time.time()

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
                    folder_count = 0

                    # Read emails from source
                    limit = int(tb_config.import_limit) if isinstance(tb_config.import_limit, (int, float)) else None
                    random_sample = tb_config.random_sample

                    async for email in source.read_emails(folder_spec, limit, random_sample):
                        # Check if already processed
                        existing = db.get_email(email.message_id)
                        if existing:
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
                            total_spam += 1
                            continue

                        # Import email
                        email_record = Email(
                            message_id=email.message_id,
                            folder_id=folder_name,
                            subject=email.subject,
                            from_addr=email.from_addr,
                            mbox_path=str(email.source_ref) if email.source_ref else "",
                            processed_at=datetime.now(),
                        )
                        db.insert_email(email_record)
                        total_imported += 1
                        folder_count += 1

                        # Classify email
                        try:
                            result = await llm.classify_email(
                                email.subject,
                                email.from_addr,
                                email.body_text,
                                folder_descriptions,
                            )
                            db.update_classification(
                                email.message_id,
                                result.predicted_folder,
                                result.confidence,
                            )
                            classifications.append((email.message_id, result.predicted_folder))
                            total_classified += 1

                            # Log progress every 10 emails
                            if total_classified % 10 == 0:
                                elapsed = time.time() - start_time
                                rate = total_classified / elapsed if elapsed > 0 else 0
                                logger.info(f"  Progress: {total_classified} classified, {rate:.1f} emails/sec")

                            # Copy/move if target available
                            if target:
                                target_folder = (
                                    result.predicted_folder
                                    if result.confidence >= min_confidence
                                    else "Unknown"
                                )

                                if move:
                                    success = await target.move_email(email.message_id, target_folder)
                                else:
                                    success = await target.copy_email(email.message_id, target_folder)

                                if success:
                                    total_copied += 1
                                    conf_str = (
                                        f" ({result.confidence:.0%})"
                                        if target_folder != "Unknown"
                                        else f" (low: {result.confidence:.0%})"
                                    )
                                    logger.info(
                                        f"  {action_past}: {email.subject[:40]}... -> {target_folder}{conf_str}"
                                    )
                                else:
                                    total_failed += 1
                                    logger.warning(f"  Failed to {action_verb.lower()}: {email.message_id}")

                        except Exception as e:
                            logger.warning(f"Failed to classify {email.message_id}: {e}")

                    logger.info(f"  Processed {folder_count} emails from {folder_name}")

            finally:
                if target:
                    await target.disconnect()

    except Exception as e:
        logger.error(f"Error during classification: {e}")
        raise

    elapsed = time.time() - start_time
    rate = total_classified / elapsed if elapsed > 0 else 0
    logger.info(
        f"Classification complete: {total_imported} imported, {total_classified} classified, {total_spam} spam"
    )
    logger.info(f"Elapsed time: {elapsed:.1f}s, rate: {rate:.2f} emails/sec")
    if target:
        logger.info(f"Target actions: {total_copied} {action_past}, {total_failed} failed")

    return classifications


async def run_bulk_classify(
    config: Config,
    db: Database,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
    websocket_port: int | None = None,
) -> None:
    """Run bulk classification mode.

    Args:
        config: Application configuration
        db: Database instance
        copy: If True, copy classified emails to target folders
        move: If True, move classified emails to target folders
        target_account: Target account for folders: 'local', 'imap', or account ID
        websocket_port: If provided, use WebSocket on this port (requires Thunderbird extension)
    """
    from ..config import WebSocketConfig
    from ..websocket_server import start_websocket_and_wait

    if copy and move:
        logger.error("Cannot specify both --copy and --move")
        return

    db.connect()
    db.init_schema()

    ws_server = None
    server_task = None

    try:
        # If copy or move requested with WebSocket, start server and wait for extension
        if (copy or move) and websocket_port is not None:
            ws_config = WebSocketConfig(
                enabled=True,
                host="localhost",
                port=websocket_port,
                auth_token=config.websocket.auth_token if config.websocket else "",
            )

            result = await start_websocket_and_wait(
                ws_config, db, config.database.categories_file
            )
            if result is None:
                return
            ws_server, server_task = result

            logger.info("Starting classification with immediate copy/move...")
        elif (copy or move) and target_account == "local":
            logger.error("Target 'local' requires --websocket. Use --target-account imap for direct IMAP.")
            return

        # Use the new abstraction-based classify function
        await bulk_classify(
            config, db, ws_server=ws_server, copy=copy, move=move, target_account=target_account
        )

    finally:
        # Stop WebSocket server if it was started
        if ws_server:
            await ws_server.stop()
        if server_task:
            server_task.cancel()
        db.close()
