"""Tests for email content cleaning module."""


from mailmap.content import clean_email_content, extract_email_summary


class TestCleanEmailContent:
    def test_empty_body(self):
        assert clean_email_content("") == ""
        assert clean_email_content(None) == ""

    def test_removes_html_tags(self):
        body = "<html><body><p>Hello world</p></body></html>"
        result = clean_email_content(body)
        assert "<" not in result
        assert "Hello world" in result

    def test_removes_html_entities(self):
        body = "Hello&nbsp;world &#160; test"
        result = clean_email_content(body)
        assert "&nbsp;" not in result
        assert "&#160;" not in result

    def test_replaces_urls(self):
        body = "Check out https://example.com/path?query=value for more info"
        result = clean_email_content(body)
        assert "[URL]" in result
        assert "https://" not in result

    def test_preserves_email_addresses(self):
        body = "Contact us at support@example.com for help"
        result = clean_email_content(body)
        assert "support@example.com" in result

    def test_removes_quoted_lines(self):
        body = "My reply\n> Original message\n>> Earlier message"
        result = clean_email_content(body)
        assert "My reply" in result
        assert "> Original" not in result
        assert ">> Earlier" not in result

    def test_removes_on_wrote_lines(self):
        body = "My reply\nOn Mon, Jan 1 2024 at 10:00 AM John wrote:\noriginal text"
        result = clean_email_content(body)
        assert "My reply" in result
        assert "wrote:" not in result.lower()

    def test_removes_header_blocks(self):
        body = "My message\nFrom: Someone\nSent: Monday\nTo: Me\nSubject: Test"
        result = clean_email_content(body)
        assert "My message" in result
        assert "From:" not in result
        assert "Sent:" not in result

    def test_removes_signature_delimiter(self):
        body = "Main content\n--\nJohn Doe\nCompany Inc"
        result = clean_email_content(body)
        assert "Main content" in result
        assert "John Doe" not in result
        assert "Company Inc" not in result

    def test_removes_mobile_signature(self):
        body = "Main content\nSent from my iPhone"
        result = clean_email_content(body)
        assert "Main content" in result
        assert "iPhone" not in result

    def test_removes_disclaimer(self):
        body = "Main content\nThis email and any attachments are confidential"
        result = clean_email_content(body)
        assert "Main content" in result
        assert "confidential" not in result

    def test_normalizes_whitespace(self):
        body = "Hello    world\n\n\n\nmultiple blanks"
        result = clean_email_content(body)
        assert "    " not in result
        assert "\n\n\n" not in result

    def test_truncates_long_content(self):
        body = "x" * 1000
        result = clean_email_content(body, max_length=100)
        assert len(result) <= 103  # Account for "..."

    def test_truncates_at_sentence(self):
        body = "First sentence. Second sentence. Third sentence is very long and continues."
        result = clean_email_content(body, max_length=50)
        assert result.endswith(".")

    def test_truncates_at_word(self):
        body = "Word1 word2 word3 word4 word5 word6 word7 word8 word9"
        result = clean_email_content(body, max_length=30)
        assert not result.endswith(" ")


class TestExtractEmailSummary:
    def test_cleans_subject(self):
        result = extract_email_summary(
            subject="Re: Fwd: Re: Original Subject",
            from_addr="sender@example.com",
            body="Body text",
        )
        assert result["subject"] == "Original Subject"

    def test_extracts_name_from_addr(self):
        result = extract_email_summary(
            subject="Test",
            from_addr="John Doe <john@example.com>",
            body="Body text",
        )
        assert result["from_addr"] == "John Doe"

    def test_cleans_quoted_name(self):
        result = extract_email_summary(
            subject="Test",
            from_addr="\"John Doe\" <john@example.com>",
            body="Body text",
        )
        assert result["from_addr"] == "John Doe"

    def test_handles_simple_email(self):
        result = extract_email_summary(
            subject="Test",
            from_addr="john@example.com",
            body="Body text",
        )
        assert result["from_addr"] == "john@example.com"

    def test_cleans_body_html(self):
        """Test that HTML tags are removed from body."""
        result = extract_email_summary(
            subject="Test",
            from_addr="sender@example.com",
            body="Hello <b>world</b> and <i>more</i>",
        )
        assert "<b>" not in result["body"]
        assert "<i>" not in result["body"]
        assert "world" in result["body"]

    def test_cleans_body_quotes(self):
        """Test that quoted lines are removed from plain text body."""
        result = extract_email_summary(
            subject="Test",
            from_addr="sender@example.com",
            body="Hello world\n> quoted text\nMore content",
        )
        assert "> quoted" not in result["body"]
        assert "Hello world" in result["body"]

    def test_handles_none_values(self):
        result = extract_email_summary(
            subject=None,
            from_addr=None,
            body=None,
        )
        assert result["subject"] == ""
        assert result["from_addr"] == ""
        assert result["body"] == ""
