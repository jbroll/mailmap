"""Tests for categories module."""


from mailmap.categories import (
    Category,
    format_categories_for_prompt,
    get_category_descriptions,
    load_categories,
    save_categories,
)


class TestCategory:
    """Tests for Category dataclass."""

    def test_category_creation(self):
        cat = Category(name="Financial", description="Banking and finance emails")
        assert cat.name == "Financial"
        assert cat.description == "Banking and finance emails"

    def test_category_str(self):
        cat = Category(name="Work", description="Work-related emails")
        assert str(cat) == "Work: Work-related emails"


class TestLoadCategories:
    """Tests for load_categories function."""

    def test_load_simple_categories(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        cat_file.write_text("""Financial: Banking and finance emails

Receipts: Purchase receipts and invoices
""")
        categories = load_categories(cat_file)
        assert len(categories) == 2
        assert categories[0].name == "Financial"
        assert categories[0].description == "Banking and finance emails"
        assert categories[1].name == "Receipts"

    def test_load_multiline_description(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        cat_file.write_text("""Financial: Banking, investments, and brokerage communications.
Includes account statements, trade confirmations, and tax documents.

Receipts: Purchase receipts
""")
        categories = load_categories(cat_file)
        assert len(categories) == 2
        assert "Banking" in categories[0].description
        assert "Includes account" in categories[0].description

    def test_load_with_comments(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        cat_file.write_text("""# Email Categories
# Edit this file to customize

Financial: Banking emails

# This is a comment
Receipts: Purchase receipts
""")
        categories = load_categories(cat_file)
        assert len(categories) == 2
        assert categories[0].name == "Financial"
        assert categories[1].name == "Receipts"

    def test_load_empty_file(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        cat_file.write_text("")
        categories = load_categories(cat_file)
        assert len(categories) == 0

    def test_load_nonexistent_file(self, tmp_path):
        cat_file = tmp_path / "nonexistent.txt"
        categories = load_categories(cat_file)
        assert len(categories) == 0

    def test_load_comments_only(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        cat_file.write_text("""# Just comments
# No actual categories
""")
        categories = load_categories(cat_file)
        assert len(categories) == 0


class TestSaveCategories:
    """Tests for save_categories function."""

    def test_save_categories(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        categories = [
            Category(name="Financial", description="Banking emails"),
            Category(name="Receipts", description="Purchase receipts"),
        ]
        save_categories(categories, cat_file)

        content = cat_file.read_text()
        assert "Financial: Banking emails" in content
        assert "Receipts: Purchase receipts" in content

    def test_save_empty_categories(self, tmp_path):
        cat_file = tmp_path / "categories.txt"
        save_categories([], cat_file)

        content = cat_file.read_text()
        assert "# Email Classification Categories" in content

    def test_roundtrip(self, tmp_path):
        """Test that save then load preserves data."""
        cat_file = tmp_path / "categories.txt"
        original = [
            Category(name="Financial", description="Banking and finance"),
            Category(name="Receipts", description="Purchase receipts and invoices"),
        ]
        save_categories(original, cat_file)
        loaded = load_categories(cat_file)

        assert len(loaded) == len(original)
        for orig, load in zip(original, loaded, strict=True):
            assert orig.name == load.name
            assert orig.description == load.description


class TestGetCategoryDescriptions:
    """Tests for get_category_descriptions function."""

    def test_get_descriptions(self):
        categories = [
            Category(name="Financial", description="Banking emails"),
            Category(name="Receipts", description="Purchase receipts"),
        ]
        descriptions = get_category_descriptions(categories)

        assert descriptions == {
            "Financial": "Banking emails",
            "Receipts": "Purchase receipts",
        }

    def test_empty_categories(self):
        descriptions = get_category_descriptions([])
        assert descriptions == {}


class TestFormatCategoriesForPrompt:
    """Tests for format_categories_for_prompt function."""

    def test_format_for_prompt(self):
        categories = [
            Category(name="Financial", description="Banking emails"),
            Category(name="Receipts", description="Purchase receipts"),
        ]
        formatted = format_categories_for_prompt(categories)

        assert "- Financial: Banking emails" in formatted
        assert "- Receipts: Purchase receipts" in formatted

    def test_format_empty(self):
        formatted = format_categories_for_prompt([])
        assert formatted == ""
