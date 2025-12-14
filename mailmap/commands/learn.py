"""Learn command - generate categories from existing folder structure."""

from __future__ import annotations

import logging
from pathlib import Path

from ..categories import Category, load_categories, save_categories
from ..config import Config
from ..llm import OllamaClient
from ..thunderbird import ThunderbirdReader

logger = logging.getLogger("mailmap")

# System folders to exclude when learning from user's folder structure
SYSTEM_FOLDERS = {
    "INBOX",
    "Inbox",
    "Sent",
    "Sent Items",
    "Sent Mail",
    "Drafts",
    "Draft",
    "Trash",
    "Deleted Items",
    "Deleted",
    "Junk",
    "Junk E-mail",
    "Spam",
    "Archive",
    "Archives",
    "All Mail",
    "Outbox",
    "Notes",
    "Calendar",
    "Contacts",
    "Tasks",
}


def is_system_folder(folder_name: str) -> bool:
    """Check if a folder is a system folder that should be excluded."""
    # Check exact match
    if folder_name in SYSTEM_FOLDERS:
        return True
    # Check if any part of a hierarchical folder matches (e.g., "INBOX/subfolder")
    parts = folder_name.replace("\\", "/").split("/")
    return any(part in SYSTEM_FOLDERS for part in parts)


async def learn_from_existing_folders(config: Config) -> None:
    """Learn classification categories from user's existing folder structure.

    This scans all non-system folders, samples emails from each, generates
    descriptions, and saves categories to categories.txt.
    """
    tb_config = config.thunderbird
    profile_path = Path(tb_config.profile_path) if tb_config.profile_path else None

    logger.info("Learning from existing folder structure...")
    try:
        reader = ThunderbirdReader(profile_path)
    except ValueError as e:
        logger.error(f"Failed to initialize Thunderbird reader: {e}")
        return

    logger.info(f"Found Thunderbird profile: {reader.profile_path}")

    # Get all folders (qualified) and extract unique folder names
    all_qualified = reader.list_folders_qualified()
    # Extract unique folder names (without server prefix)
    seen_names: set[str] = set()
    user_folders: list[tuple[str, str]] = []
    for qualified in all_qualified:
        _, folder_name = qualified.split(":", 1) if ":" in qualified else (None, qualified)
        if folder_name not in seen_names and not is_system_folder(folder_name):
            seen_names.add(folder_name)
            user_folders.append((qualified, folder_name))

    system_folders = [f.split(":", 1)[1] if ":" in f else f for f in all_qualified
                      if is_system_folder(f.split(":", 1)[1] if ":" in f else f)]
    system_folders = list(set(system_folders))

    logger.info(f"Found {len(all_qualified)} total folders")
    logger.info(f"System folders (excluded): {system_folders}")
    logger.info(f"User folders to learn from: {[name for _, name in user_folders]}")

    if not user_folders:
        logger.warning("No user folders found to learn from")
        return

    # Load existing categories to merge with
    categories_path = Path(config.database.categories_file)
    existing_categories = load_categories(categories_path)
    existing_names = {cat.name for cat in existing_categories}
    new_categories: list[Category] = []

    for folder_spec, folder_name in user_folders:
        # Skip if category already exists
        if folder_name in existing_names:
            logger.info(f"Category '{folder_name}' already exists, skipping")
            continue

        logger.info(f"Processing folder: {folder_name}")

        # Sample emails from this folder for description generation
        samples = reader.get_sample_emails(
            folder_spec,
            count=tb_config.samples_per_folder,
        )

        if not samples:
            logger.info(f"  No emails in {folder_name}, skipping")
            continue

        # Generate folder description from samples
        sample_dicts = [
            {
                "subject": s.subject,
                "from_addr": s.from_addr,
                "body": s.body_text[:500],
            }
            for s in samples
        ]

        async with OllamaClient(config.ollama) as llm:
            result = await llm.generate_folder_description(folder_name, sample_dicts)

        new_categories.append(Category(name=folder_name, description=result.description))
        logger.info(f"  Created category '{folder_name}': {result.description[:60]}...")

    # Merge and save categories
    all_categories = existing_categories + new_categories
    save_categories(all_categories, categories_path)

    logger.info(f"Learning complete: {len(new_categories)} new categories added")
    logger.info(f"Total categories: {len(all_categories)} (saved to {categories_path})")


async def run_learn_folders(config: Config) -> None:
    """Run learn-folders mode."""
    await learn_from_existing_folders(config)
