"""Init command - analyze emails and suggest folder structure."""

from __future__ import annotations

import logging
from pathlib import Path

from ..categories import Category, save_categories
from ..config import Config
from ..llm import OllamaClient, SuggestedFolder

logger = logging.getLogger("mailmap")


async def init_folders_from_samples(config: Config) -> None:
    """Analyze sample emails iteratively in batches to build folder structure."""
    from ..sources import select_source

    tb_config = config.thunderbird

    logger.info("Initializing folder structure from email samples (iterative batching)...")

    # Select source automatically
    try:
        source = select_source(config, config.thunderbird.source_type)
        logger.info(f"Using {source.source_type} source")
    except ValueError as e:
        logger.error(str(e))
        return

    # Collect sample emails
    sample_limit = tb_config.init_sample_limit
    all_emails: list[dict] = []

    async with source:
        # Get folders to process
        all_folders = await source.list_folders()

        if tb_config.folder_filter:
            # Filter to specific folder (handle server:folder syntax)
            filter_folder = tb_config.folder_filter
            matching = [f for f in all_folders if f == filter_folder or f.endswith(f":{filter_folder}")]
            if not matching:
                logger.error(f"Folder '{filter_folder}' not found")
                return
            if len(matching) > 1:
                logger.error(
                    f"Folder '{filter_folder}' found in multiple accounts: {matching}. "
                    f"Use server:folder syntax."
                )
                return
            folders = matching
            logger.info(f"Reading from folder: {filter_folder}")
        else:
            folders = all_folders

        for folder_spec in folders:
            # Calculate limit for this folder
            if isinstance(sample_limit, float) and sample_limit < 1:
                # Percentage-based (handled by source)
                limit = int(sample_limit * 1000)  # Estimate
                random_sample = True
            elif tb_config.random_sample:
                limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
                random_sample = True
            else:
                limit = int(sample_limit) if len(folders) == 1 else max(50, int(sample_limit) // len(folders))
                random_sample = False

            count = 0
            async for email in source.read_emails(folder_spec, limit, random_sample):
                all_emails.append({
                    "subject": email.subject,
                    "from_addr": email.from_addr,
                    "body": email.body_text[:300],
                })
                count += 1

            if random_sample:
                logger.info(f"Sampled {count} emails from {folder_spec}")

    if not all_emails:
        logger.error("No emails found to analyze")
        return

    logger.info(f"Collected {len(all_emails)} emails, processing in batches...")

    # Process in batches, refining categories iteratively
    batch_size = 100
    categories: list[SuggestedFolder] = []
    all_assignments: list[dict] = []
    batch_num = 0

    async with OllamaClient(config.ollama) as llm:
        for batch_num, start_idx in enumerate(range(0, len(all_emails), batch_size), 1):
            batch = all_emails[start_idx:start_idx + batch_size]

            categories, assignments = await llm.refine_folder_structure(
                batch,
                categories,
                batch_num,
                batch_size,
            )

            all_assignments.extend(assignments)
            logger.info(f"Batch {batch_num}: {len(categories)} categories after processing {len(batch)} emails")

        # Normalize categories to merge duplicates
        logger.info("Normalizing categories to merge duplicates...")
        categories, rename_map = await llm.normalize_categories(categories)
        logger.info(f"After normalization: {len(categories)} categories")

        # Apply rename map to all assignments
        for assignment in all_assignments:
            old_cat = assignment.get("category", "")
            if old_cat in rename_map:
                assignment["category"] = rename_map[old_cat]

    # Display final categories
    print(f"\nProcessed {len(all_emails)} emails in {batch_num} batches.")
    print(f"Final folder structure ({len(categories)} categories):\n")

    for i, folder in enumerate(categories, 1):
        print(f"  {i}. {folder.name}")
        print(f"     {folder.description}")

    print()

    # Count assignments per category
    category_counts: dict[str, int] = {}
    for assignment in all_assignments:
        cat = assignment.get("category", "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    if category_counts:
        print("Email distribution:")
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")
        print()

    # Save categories to file
    categories_path = Path(config.database.categories_file)
    new_categories = [
        Category(name=folder.name, description=folder.description)
        for folder in categories
    ]
    save_categories(new_categories, categories_path)
    print(f"Saved {len(categories)} categories to {categories_path}")


async def run_init_folders(config: Config) -> None:
    """Run folder initialization mode."""
    await init_folders_from_samples(config)
