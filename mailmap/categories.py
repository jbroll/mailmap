"""Category management for email classification.

Categories are stored in a simple text file format that's easy to edit
and LLM-friendly. The format is:

    CategoryName: Description of what emails belong in this category.

    AnotherCategory: Another description that can span
    multiple lines until the next blank line or category.

Lines starting with # are comments.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Category:
    """An email classification category."""
    name: str
    description: str

    def __str__(self) -> str:
        return f"{self.name}: {self.description}"


def load_categories(path: str | Path) -> list[Category]:
    """Load categories from a text file.

    Format:
        CategoryName: Description text that can span
        multiple lines.

        AnotherCategory: Another description.

    Args:
        path: Path to categories file

    Returns:
        List of Category objects
    """
    path = Path(path)
    if not path.exists():
        return []

    categories = []
    current_name = None
    current_desc_lines = []

    def save_current():
        nonlocal current_name, current_desc_lines
        if current_name:
            desc = " ".join(current_desc_lines).strip()
            if desc:
                categories.append(Category(name=current_name, description=desc))
        current_name = None
        current_desc_lines = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()

            # Skip comments
            if line.startswith("#"):
                continue

            # Blank line ends current category
            if not line.strip():
                save_current()
                continue

            # Check for new category (Name: Description)
            if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
                # Could be a new category
                colon_idx = line.index(":")
                potential_name = line[:colon_idx].strip()

                # Valid category names: no spaces, alphanumeric + underscore
                if potential_name and " " not in potential_name:
                    save_current()
                    current_name = potential_name
                    current_desc_lines = [line[colon_idx + 1:].strip()]
                    continue

            # Continuation of current description
            if current_name:
                current_desc_lines.append(line.strip())

    # Don't forget the last category
    save_current()

    return categories


def save_categories(categories: list[Category], path: str | Path) -> None:
    """Save categories to a text file.

    Args:
        categories: List of Category objects
        path: Path to save to
    """
    path = Path(path)
    lines = [
        "# Email Classification Categories",
        "# Format: CategoryName: Description",
        "#",
        "# Edit this file to customize categories. The LLM will use these",
        "# descriptions to classify emails into the appropriate category.",
        "",
    ]

    for cat in categories:
        lines.append(f"{cat.name}: {cat.description}")
        lines.append("")

    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def get_category_descriptions(categories: list[Category]) -> dict[str, str]:
    """Convert categories to a dict for LLM classification.

    Args:
        categories: List of Category objects

    Returns:
        Dict of category name -> description
    """
    return {cat.name: cat.description for cat in categories}


def format_categories_for_prompt(categories: list[Category]) -> str:
    """Format categories as text for inclusion in LLM prompts.

    Args:
        categories: List of Category objects

    Returns:
        Formatted string listing all categories
    """
    lines = []
    for cat in categories:
        lines.append(f"- {cat.name}: {cat.description}")
    return "\n".join(lines)
