"""CLI entry point for mailmap."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .commands import (
    apply_cli_overrides,
    cleanup_thunderbird_folders,
    clear_cmd,
    copy_email_cmd,
    create_folder_cmd,
    delete_folder_cmd,
    list_categories_cmd,
    list_classifications,
    list_emails_cmd,
    list_folders_cmd,
    move_email_cmd,
    read_email_cmd,
    reset_database,
    run_bulk_classify,
    run_daemon,
    run_init_folders,
    run_learn_folders,
    summary_cmd,
    upload_to_imap,
)
from .config import load_config
from .database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mailmap")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a parser."""
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to configuration file",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Override database path",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        help="Override Ollama base URL",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        help="Override Ollama model name",
    )


def add_thunderbird_args(parser: argparse.ArgumentParser) -> None:
    """Add Thunderbird-related arguments to a parser."""
    parser.add_argument(
        "--profile",
        type=str,
        dest="thunderbird_profile",
        help="Thunderbird profile path",
    )
    parser.add_argument(
        "--folder",
        type=str,
        dest="thunderbird_folder",
        help="Process only this folder (e.g., INBOX or server.com:INBOX)",
    )
    parser.add_argument(
        "--source-type",
        type=str,
        choices=["thunderbird", "imap"],
        dest="source_type",
        help="Email source: 'thunderbird' (local cache, default) or 'imap' (direct connection)",
    )


def add_limit_args(parser: argparse.ArgumentParser) -> None:
    """Add limit-related arguments to a parser."""
    parser.add_argument(
        "--limit",
        type=float,
        dest="import_limit",
        help="Max emails: integer for count, fraction for percentage (0.1 = 10%%)",
    )
    parser.add_argument(
        "--random",
        action="store_true",
        help="Randomly sample emails instead of sequential",
    )


def add_target_args(parser: argparse.ArgumentParser) -> None:
    """Add target account and websocket arguments to a parser."""
    parser.add_argument(
        "--target-account",
        type=str,
        default="local",
        help="Target account: 'local' (default), 'imap', or IMAP server name",
    )
    parser.add_argument(
        "--websocket",
        type=int,
        nargs="?",
        const=9753,
        metavar="PORT",
        help="Use WebSocket target (requires Thunderbird extension). Default port: 9753",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        description="Mailmap email classification system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Add common args to main parser for when no subcommand is given
    add_common_args(parser)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # daemon - Run IMAP daemon (default)
    daemon_parser = subparsers.add_parser("daemon", help="Run IMAP listener daemon")
    add_common_args(daemon_parser)
    daemon_parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Process existing unclassified emails in monitored folders on startup",
    )
    daemon_parser.add_argument(
        "--move",
        action="store_true",
        help="Move classified emails to their destination IMAP folders",
    )

    # learn - Learn categories from existing folders (saves to categories.txt)
    learn_parser = subparsers.add_parser("learn", help="Learn categories from existing Thunderbird folders")
    add_common_args(learn_parser)
    add_thunderbird_args(learn_parser)

    # classify - Bulk classify emails from Thunderbird
    classify_parser = subparsers.add_parser("classify", help="Bulk classify emails from Thunderbird")
    add_common_args(classify_parser)
    add_thunderbird_args(classify_parser)
    add_limit_args(classify_parser)
    classify_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy classified emails to target folders via Thunderbird extension",
    )
    classify_parser.add_argument(
        "--move",
        action="store_true",
        help="Move classified emails to target folders via Thunderbird extension",
    )
    add_target_args(classify_parser)

    # init - Initialize folder structure
    init_parser = subparsers.add_parser("init", help="Analyze emails and suggest folder structure")
    add_common_args(init_parser)
    add_thunderbird_args(init_parser)
    add_limit_args(init_parser)

    # upload - Upload to IMAP
    upload_parser = subparsers.add_parser("upload", help="Upload classified emails to IMAP folders")
    add_common_args(upload_parser)
    upload_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without uploading",
    )
    upload_parser.add_argument(
        "--folder",
        type=str,
        dest="upload_folder",
        help="Only upload emails classified to this folder",
    )

    # list - List classifications
    list_parser = subparsers.add_parser("list", help="List classification results")
    add_common_args(list_parser)
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum results to show (default: 50)",
    )

    # categories - List categories
    categories_parser = subparsers.add_parser("categories", help="List classification categories")
    add_common_args(categories_parser)

    # summary - Classification summary
    summary_parser = subparsers.add_parser("summary", help="Show classification summary with counts")
    add_common_args(summary_parser)

    # clear - Clear classifications
    clear_parser = subparsers.add_parser("clear", help="Clear email classifications")
    add_common_args(clear_parser)
    clear_parser.add_argument(
        "--folder",
        type=str,
        help="Only clear emails from this source folder",
    )

    # reset - Reset database
    reset_parser = subparsers.add_parser("reset", help="Delete database and start fresh")
    add_common_args(reset_parser)

    # cleanup - Delete classification folders from Thunderbird
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Delete classification folders from Thunderbird Local Folders"
    )
    add_common_args(cleanup_parser)
    add_target_args(cleanup_parser)

    # folders - List folders with counts
    folders_parser = subparsers.add_parser("folders", help="List folders with email counts")
    add_common_args(folders_parser)
    folders_parser.add_argument(
        "--source-type",
        choices=["imap", "thunderbird"],
        default="imap",
        help="Email source: 'imap' (default) or 'thunderbird'",
    )

    # emails - List emails in a folder
    emails_parser = subparsers.add_parser("emails", help="List emails in a folder")
    add_common_args(emails_parser)
    emails_parser.add_argument("folder", help="Folder name")
    emails_parser.add_argument(
        "--source-type",
        choices=["imap", "thunderbird"],
        default="imap",
        help="Email source: 'imap' (default) or 'thunderbird'",
    )
    emails_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum emails to list (default: 50)",
    )

    # read - Read/view an email
    read_parser = subparsers.add_parser("read", help="Read and display an email")
    add_common_args(read_parser)
    read_parser.add_argument("folder", help="Folder name")
    read_parser.add_argument("uid", type=int, help="Email UID")

    # create-folder - Create a folder
    create_folder_parser = subparsers.add_parser("create-folder", help="Create a folder on IMAP server")
    add_common_args(create_folder_parser)
    create_folder_parser.add_argument("folder", help="Folder name to create")

    # delete-folder - Delete a folder
    delete_folder_parser = subparsers.add_parser("delete-folder", help="Delete a folder from IMAP server")
    add_common_args(delete_folder_parser)
    delete_folder_parser.add_argument("folder", help="Folder name to delete")

    # move - Move an email
    move_parser = subparsers.add_parser("move", help="Move an email to another folder")
    add_common_args(move_parser)
    move_parser.add_argument("folder", help="Source folder")
    move_parser.add_argument("uid", type=int, help="Email UID")
    move_parser.add_argument("dest", help="Destination folder")

    # copy - Copy an email
    copy_parser = subparsers.add_parser("copy", help="Copy an email to another folder")
    add_common_args(copy_parser)
    copy_parser.add_argument("folder", help="Source folder")
    copy_parser.add_argument("uid", type=int, help="Email UID")
    copy_parser.add_argument("dest", help="Destination folder")

    return parser


def main() -> None:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Show help if no command specified
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if not args.config.exists():
        logger.error(f"Configuration file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    config = apply_cli_overrides(config, args)

    # Handle reset before creating Database object
    if args.command == "reset":
        reset_database(Path(config.database.path))
        sys.exit(0)

    db = Database(config.database.path)

    if args.command == "list":
        limit = getattr(args, "limit", 50)
        list_classifications(db, limit=limit)
    elif args.command == "categories":
        list_categories_cmd(config)
    elif args.command == "summary":
        summary_cmd(db)
    elif args.command == "clear":
        folder = getattr(args, "folder", None)
        clear_cmd(db, folder)
    elif args.command == "init":
        asyncio.run(run_init_folders(config))
    elif args.command == "learn":
        asyncio.run(run_learn_folders(config))
    elif args.command == "classify":
        copy_mode = getattr(args, "copy", False)
        move_mode = getattr(args, "move", False)
        target_account = getattr(args, "target_account", "local")
        websocket_port = getattr(args, "websocket", None)
        asyncio.run(run_bulk_classify(config, db, copy=copy_mode, move=move_mode, target_account=target_account, websocket_port=websocket_port))
    elif args.command == "upload":
        dry_run = getattr(args, "dry_run", False)
        folder_filter = getattr(args, "upload_folder", None)
        upload_to_imap(config, db, dry_run=dry_run, folder_filter=folder_filter)
    elif args.command == "cleanup":
        target_account = getattr(args, "target_account", "local")
        asyncio.run(cleanup_thunderbird_folders(config, db, target_account=target_account))
    elif args.command == "daemon":
        process_existing = getattr(args, "process_existing", False)
        move_emails = getattr(args, "move", False)
        asyncio.run(run_daemon(config, db, process_existing=process_existing, move=move_emails))
    elif args.command == "folders":
        source_type = getattr(args, "source_type", "imap")
        asyncio.run(list_folders_cmd(config, source_type))
    elif args.command == "emails":
        folder = args.folder
        source_type = getattr(args, "source_type", "imap")
        limit = getattr(args, "limit", 50)
        asyncio.run(list_emails_cmd(config, folder, source_type, limit))
    elif args.command == "read":
        asyncio.run(read_email_cmd(config, args.folder, args.uid))
    elif args.command == "create-folder":
        create_folder_cmd(config, args.folder)
    elif args.command == "delete-folder":
        delete_folder_cmd(config, args.folder)
    elif args.command == "move":
        move_email_cmd(config, args.folder, args.uid, args.dest)
    elif args.command == "copy":
        copy_email_cmd(config, args.folder, args.uid, args.dest)


if __name__ == "__main__":
    main()
