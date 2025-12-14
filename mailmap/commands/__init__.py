"""Command implementations for mailmap CLI."""

from .classify import bulk_classify, run_bulk_classify
from .daemon import EmailProcessor, run_daemon, run_listener
from .imap_ops import (
    copy_email_cmd,
    create_folder_cmd,
    delete_folder_cmd,
    list_emails_cmd,
    list_folders_cmd,
    move_email_cmd,
    read_email_cmd,
)
from .init import init_folders_from_samples, run_init_folders
from .learn import learn_from_existing_folders, run_learn_folders
from .upload import cleanup_folders, cleanup_thunderbird_folders, upload_to_imap
from .utils import (
    apply_cli_overrides,
    clear_cmd,
    list_categories_cmd,
    list_classifications,
    reset_database,
    summary_cmd,
    sync_transfers,
)

__all__ = [
    # classify
    "bulk_classify",
    "run_bulk_classify",
    # daemon
    "EmailProcessor",
    "run_daemon",
    "run_listener",
    # init
    "init_folders_from_samples",
    "run_init_folders",
    # imap_ops
    "copy_email_cmd",
    "create_folder_cmd",
    "delete_folder_cmd",
    "list_emails_cmd",
    "list_folders_cmd",
    "move_email_cmd",
    "read_email_cmd",
    # learn
    "learn_from_existing_folders",
    "run_learn_folders",
    # upload
    "cleanup_folders",
    "cleanup_thunderbird_folders",
    "upload_to_imap",
    # utils
    "apply_cli_overrides",
    "clear_cmd",
    "list_categories_cmd",
    "list_classifications",
    "reset_database",
    "summary_cmd",
    "sync_transfers",
]
