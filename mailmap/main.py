"""Main entry point and orchestration for mailmap.

This module re-exports functions from the commands package for backward compatibility.
The actual implementations are in the mailmap.commands subpackage.
"""

from __future__ import annotations

# Re-export from cli for the entry point
from .cli import main

# Re-export from commands for backward compatibility
from .commands import (
    EmailProcessor,
    apply_cli_overrides,
    bulk_classify,
    cleanup_thunderbird_folders,
    clear_cmd,
    copy_email_cmd,
    create_folder_cmd,
    delete_folder_cmd,
    init_folders_from_samples,
    learn_from_existing_folders,
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
    run_listener,
    summary_cmd,
    upload_to_imap,
)

# Re-export is_system_folder from learn module
from .commands.learn import SYSTEM_FOLDERS, is_system_folder

__all__ = [
    # Entry point
    "main",
    # Daemon
    "EmailProcessor",
    "run_daemon",
    "run_listener",
    # Learn
    "SYSTEM_FOLDERS",
    "is_system_folder",
    "learn_from_existing_folders",
    "run_learn_folders",
    # Classify
    "bulk_classify",
    "run_bulk_classify",
    # Init
    "init_folders_from_samples",
    "run_init_folders",
    # Upload
    "cleanup_thunderbird_folders",
    "upload_to_imap",
    # IMAP ops
    "copy_email_cmd",
    "create_folder_cmd",
    "delete_folder_cmd",
    "list_emails_cmd",
    "list_folders_cmd",
    "move_email_cmd",
    "read_email_cmd",
    # Utils
    "apply_cli_overrides",
    "clear_cmd",
    "list_categories_cmd",
    "list_classifications",
    "reset_database",
    "summary_cmd",
]

if __name__ == "__main__":
    main()
