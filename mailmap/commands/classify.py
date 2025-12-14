"""Classify command - bulk email classification."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..categories import get_category_descriptions, load_categories
from ..config import Config
from ..database import Database, Email
from ..llm import OllamaClient
from ..spam import is_spam, parse_rules
from ..thunderbird import ThunderbirdReader

if TYPE_CHECKING:
    from ..websocket_server import WebSocketServer

logger = logging.getLogger("mailmap")


async def bulk_classify_from_thunderbird(
    config: Config,
    db: Database,
    ws_server: WebSocketServer | None = None,
    move: bool = False,
    target_account: str = "local",
    min_confidence: float = 0.5,
) -> list[tuple[str, str]]:
    """Bulk classify emails from Thunderbird using existing categories.

    Reads emails from Thunderbird profile and classifies them using
    the categories defined in categories.txt.

    Args:
        config: Application configuration
        db: Database instance
        ws_server: Optional WebSocket server for immediate copy/move after classification
        min_confidence: Minimum confidence to copy/move (below this goes to Unknown)
        move: If True with ws_server, move messages; otherwise copy
        target_account: Target account for folders when using ws_server

    Returns:
        List of (message_id, classification) tuples for successfully classified emails.
    """
    from ..protocol import Action

    classifications: list[tuple[str, str]] = []
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    # Load categories
    categories_path = Path(config.database.categories_file)
    categories = load_categories(categories_path)
    folder_descriptions = get_category_descriptions(categories)

    if not folder_descriptions:
        logger.error(f"No categories found in {categories_path}")
        logger.error("Run 'mailmap learn' first to generate categories")
        return classifications

    logger.info(f"Loaded {len(folder_descriptions)} categories from {categories_path}")

    # Initialize Thunderbird reader
    logger.info("Initializing Thunderbird reader...")
    try:
        reader = ThunderbirdReader(profile_path)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return classifications

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # Get folders to process
    if tb_config.folder_filter:
        # Validate folder upfront (will error if ambiguous)
        try:
            server, folder_name = reader.resolve_folder(tb_config.folder_filter)
            # Use server:folder format for unambiguous access
            folders = [f"{server}:{folder_name}"]
            logger.info(f"Processing folder: {folder_name} (from {server})")
        except ValueError as e:
            logger.error(str(e))
            return classifications
    else:
        # Get all folders with server prefix to avoid ambiguity
        folders = reader.list_folders_qualified()

    # Load spam rules
    spam_rules = parse_rules(config.spam.rules) if config.spam.enabled else []

    total_imported = 0
    total_classified = 0
    total_copied = 0
    total_failed = 0
    total_spam = 0

    action = Action.MOVE_MESSAGES if move else Action.COPY_MESSAGES
    action_verb = "Moving" if move else "Copying"
    action_past = "moved" if move else "copied"

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

        # Choose between random sampling and sequential reading
        limit = int(tb_config.import_limit) if isinstance(tb_config.import_limit, (int, float)) else None
        if tb_config.random_sample and limit:
            email_iterator = reader.read_folder_random(folder_spec, limit)
        else:
            email_iterator = reader.read_folder(folder_spec, limit=limit)

        for tb_email in email_iterator:
            # Check if already processed
            existing = db.get_email(tb_email.message_id)
            if existing:
                continue

            # Check for spam
            headers = tb_email.headers or {}
            is_spam_result, spam_reason = is_spam(headers, spam_rules) if spam_rules else (False, None)
            if is_spam_result:
                email_record = Email(
                    message_id=tb_email.message_id,
                    folder_id=folder_name,
                    subject=tb_email.subject,
                    from_addr=tb_email.from_addr,
                    mbox_path=tb_email.mbox_path,
                    is_spam=True,
                    spam_reason=spam_reason,
                    processed_at=datetime.now(),
                )
                db.insert_email(email_record)
                total_spam += 1
                continue

            # Import email
            email_record = Email(
                message_id=tb_email.message_id,
                folder_id=folder_name,
                subject=tb_email.subject,
                from_addr=tb_email.from_addr,
                mbox_path=tb_email.mbox_path,
                processed_at=datetime.now(),
            )
            db.insert_email(email_record)
            total_imported += 1
            folder_count += 1

            # Classify email
            try:
                async with OllamaClient(config.ollama) as llm:
                    result = await llm.classify_email(
                        tb_email.subject,
                        tb_email.from_addr,
                        tb_email.body_text,
                        folder_descriptions,
                    )
                db.update_classification(
                    tb_email.message_id,
                    result.predicted_folder,
                    result.confidence,
                )
                classifications.append((tb_email.message_id, result.predicted_folder))
                total_classified += 1

                # Immediately copy/move if WebSocket server is connected
                if ws_server and ws_server.is_connected:
                    # Use predicted folder if confidence is high enough, otherwise Unknown
                    target_folder = result.predicted_folder if result.confidence >= min_confidence else "Unknown"
                    target_folder_spec = {"path": target_folder, "accountId": target_account}
                    response = await ws_server.send_request(
                        action,
                        {
                            "headerMessageIds": [tb_email.message_id],
                            "targetFolder": target_folder_spec,
                        },
                        timeout=30.0,
                    )
                    if response and response.ok:
                        result_data = response.result or {}
                        success_count = result_data.get("moved" if move else "copied", 0)
                        total_copied += success_count
                        if success_count:
                            conf_str = f" ({result.confidence:.0%})" if target_folder != "Unknown" else f" (low: {result.confidence:.0%})"
                            logger.info(f"  {action_past}: {tb_email.subject[:40]}... -> {target_folder}{conf_str}")
                        else:
                            not_found = result_data.get("notFound", [])
                            if not_found:
                                logger.warning(f"  Not found in Thunderbird: {tb_email.message_id}")
                                total_failed += 1
                    else:
                        error = response.error if response else "No response"
                        logger.error(f"  Failed to {action_verb.lower()}: {error}")
                        total_failed += 1

            except Exception as e:
                logger.warning(f"Failed to classify {tb_email.message_id}: {e}")

        logger.info(f"  Processed {folder_count} emails from {folder_name}")

    logger.info(f"Classification complete: {total_imported} imported, {total_classified} classified, {total_spam} spam")
    if ws_server:
        logger.info(f"Extension actions: {total_copied} {action_past}, {total_failed} failed")
    return classifications


async def bulk_classify(
    config: Config,
    db: Database,
    ws_server: WebSocketServer | None = None,
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
        move: If True with target, move messages; otherwise copy
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
    if ws_server:
        try:
            target = select_target(config, ws_server, target_account)
            logger.info(f"Using {target.target_type} target (account: {target_account})")
        except ValueError as e:
            logger.error(str(e))
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

    try:
        async with source:
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
                            async with OllamaClient(config.ollama) as llm:
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

    logger.info(
        f"Classification complete: {total_imported} imported, {total_classified} classified, {total_spam} spam"
    )
    if target:
        logger.info(f"Target actions: {total_copied} {action_past}, {total_failed} failed")

    return classifications


async def run_bulk_classify(
    config: Config,
    db: Database,
    copy: bool = False,
    move: bool = False,
    target_account: str = "local",
) -> None:
    """Run bulk classification mode.

    Args:
        config: Application configuration
        db: Database instance
        copy: If True, copy classified emails to target folders via extension
        move: If True, move classified emails to target folders via extension
        target_account: Target account for folders: 'local', 'imap', or account ID
    """
    from ..websocket_server import WebSocketServer

    if copy and move:
        logger.error("Cannot specify both --copy and --move")
        return

    db.connect()
    db.init_schema()

    ws_server = None
    server_task = None

    try:
        # If copy or move requested, start WebSocket server and wait for extension
        if copy or move:
            ws_config = config.websocket
            if not ws_config.enabled:
                logger.error("WebSocket is not enabled in config. Add [websocket] section with enabled=true")
                return

            ws_server = WebSocketServer(ws_config, db, config.database.categories_file)
            server_task = asyncio.create_task(ws_server.start())

            logger.info(f"WebSocket server started on ws://{ws_config.host}:{ws_config.port}")
            logger.info("Waiting for Thunderbird extension to connect...")

            # Wait for extension to connect (timeout after 60 seconds)
            for _ in range(60):
                if ws_server.is_connected:
                    break
                await asyncio.sleep(1)
            else:
                logger.error("Timeout waiting for extension to connect")
                await ws_server.stop()
                server_task.cancel()
                return

            logger.info("Extension connected! Starting classification with immediate copy/move...")

        # Use the new abstraction-based classify function
        await bulk_classify(
            config, db, ws_server=ws_server, move=move, target_account=target_account
        )

    finally:
        # Stop WebSocket server if it was started
        if ws_server:
            await ws_server.stop()
        if server_task:
            server_task.cancel()
        db.close()
