"""Utility commands - list, summary, clear, reset, and config helpers."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..categories import load_categories
from ..config import Config
from ..database import Database

logger = logging.getLogger("mailmap")


def list_classifications(db: Database, limit: int = 50) -> None:
    """List classification results from the database."""
    with db:
        emails = db.get_recent_classifications(limit)

        if not emails:
            print("No classification results found.")
            return

        print(f"{'Subject':<40} {'From':<25} {'Original':<15} {'Predicted':<15} {'Conf':<6}")
        print("-" * 105)

        for email in emails:
            subject = (email.subject or "")[:38]
            from_addr = (email.from_addr or "")[:23]
            folder = (email.folder_id or "")[:13]
            classification = (email.classification or "")[:13]
            confidence = email.confidence or 0

            print(f"{subject:<40} {from_addr:<25} {folder:<15} {classification:<15} {confidence:.2f}")

        print(f"\nTotal: {len(emails)} results (showing up to {limit})")


def list_categories_cmd(config: Config) -> None:
    """List categories from categories file."""
    categories_path = Path(config.database.categories_file)
    categories = load_categories(categories_path)

    if not categories:
        print(f"No categories found in {categories_path}")
        print("Create a categories.txt file or run 'mailmap init' to generate categories.")
        return

    print(f"{'Category':<25} {'Description':<75}")
    print("-" * 102)

    for cat in categories:
        name = cat.name[:23]
        desc = cat.description[:73]
        print(f"{name:<25} {desc:<75}")

    print(f"\nTotal: {len(categories)} categories (from {categories_path})")


def clear_cmd(db: Database, folder: str | None = None) -> None:
    """Clear classifications from emails."""
    db.connect()
    db.init_schema()
    try:
        count = db.clear_classifications(folder)
        if folder:
            print(f"Cleared classifications from {count} emails in folder '{folder}'")
        else:
            print(f"Cleared classifications from {count} emails")
    finally:
        db.close()


def summary_cmd(db: Database) -> None:
    """Show classification summary with counts per category."""
    with db:
        total = db.get_total_count()
        classified = db.get_classified_count()
        spam = db.get_spam_count()
        unclassified = total - classified - spam

        summary = db.get_classification_summary()

        if not summary and spam == 0:
            print("No classified emails found.")
            return

        print(f"{'Category':<35} {'Count':>8} {'Percent':>8}")
        print("-" * 53)

        for category, count in summary:
            category_str = (category or "")[:33]
            pct = 100 * count / total if total > 0 else 0
            print(f"{category_str:<35} {count:>8} {pct:>7.1f}%")

        print("-" * 53)
        print(f"{'Classified':<35} {classified:>8} {100*classified/total if total else 0:>7.1f}%")
        if spam > 0:
            print(f"{'Spam (skipped)':<35} {spam:>8} {100*spam/total if total else 0:>7.1f}%")
        if unclassified > 0:
            print(f"{'Unclassified':<35} {unclassified:>8} {100*unclassified/total if total else 0:>7.1f}%")
        print(f"{'Total':<35} {total:>8}")


def reset_database(db_path: Path) -> None:
    """Delete the database file to start fresh."""
    if db_path.exists():
        db_path.unlink()
        logger.info(f"Deleted database: {db_path}")
    else:
        logger.info(f"Database does not exist: {db_path}")


def apply_cli_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Apply command-line overrides to config."""
    if getattr(args, "db_path", None):
        config.database.path = args.db_path
    if getattr(args, "ollama_url", None):
        config.ollama.base_url = args.ollama_url
    if getattr(args, "ollama_model", None):
        config.ollama.model = args.ollama_model
    if getattr(args, "thunderbird_profile", None):
        config.thunderbird.profile_path = args.thunderbird_profile
    if getattr(args, "thunderbird_folder", None):
        config.thunderbird.folder_filter = args.thunderbird_folder
    if getattr(args, "samples_per_folder", None) is not None:
        config.thunderbird.samples_per_folder = args.samples_per_folder
    if getattr(args, "random", False):
        config.thunderbird.random_sample = True
    if getattr(args, "source_type", None):
        config.thunderbird.source_type = args.source_type

    # Handle --limit (used for import_limit and init_sample_limit)
    import_limit = getattr(args, "import_limit", None)
    if import_limit is not None:
        # If >= 1, treat as integer count; if < 1, treat as percentage
        if import_limit >= 1:
            config.thunderbird.import_limit = int(import_limit)
            config.thunderbird.init_sample_limit = int(import_limit)
        else:
            config.thunderbird.import_limit = import_limit
            config.thunderbird.init_sample_limit = import_limit

    return config
