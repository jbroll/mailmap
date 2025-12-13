"""Tests for spam rule parser and checker."""

import pytest

from mailmap.spam import (
    Operator,
    RuleParseError,
    check_rule,
    is_spam,
    parse_rule,
    parse_rules,
)


class TestParseRule:
    """Tests for parse_rule function."""

    def test_numeric_gte(self):
        """Test parsing numeric >= rule."""
        rule = parse_rule("X-MS-Exchange-Organization-SCL >= 5")
        assert rule.header == "X-MS-Exchange-Organization-SCL"
        assert rule.operator == Operator.GTE
        assert rule.value == 5
        assert rule.pattern is None

    def test_numeric_gt(self):
        """Test parsing numeric > rule."""
        rule = parse_rule("X-Spam-Score > 3.5")
        assert rule.header == "X-Spam-Score"
        assert rule.operator == Operator.GT
        assert rule.value == 3.5

    def test_numeric_lt(self):
        """Test parsing numeric < rule."""
        rule = parse_rule("X-Some-Score < 10")
        assert rule.operator == Operator.LT
        assert rule.value == 10

    def test_numeric_lte(self):
        """Test parsing numeric <= rule."""
        rule = parse_rule("X-Score <= 2.5")
        assert rule.operator == Operator.LTE
        assert rule.value == 2.5

    def test_string_eq(self):
        """Test parsing string == rule."""
        rule = parse_rule("X-Spam-Flag == YES")
        assert rule.header == "X-Spam-Flag"
        assert rule.operator == Operator.EQ
        assert rule.value == "YES"

    def test_string_ne(self):
        """Test parsing string != rule."""
        rule = parse_rule("X-Spam-Status != No")
        assert rule.operator == Operator.NE
        assert rule.value == "No"

    def test_prefix(self):
        """Test parsing prefix rule."""
        rule = parse_rule("X-Spam-Status prefix Yes")
        assert rule.header == "X-Spam-Status"
        assert rule.operator == Operator.PREFIX
        assert rule.value == "Yes"

    def test_suffix(self):
        """Test parsing suffix rule."""
        rule = parse_rule("X-Result suffix spam")
        assert rule.operator == Operator.SUFFIX
        assert rule.value == "spam"

    def test_contains(self):
        """Test parsing contains rule."""
        rule = parse_rule("X-Proofpoint-Spam-Details contains rule=spam")
        assert rule.header == "X-Proofpoint-Spam-Details"
        assert rule.operator == Operator.CONTAINS
        assert rule.value == "rule=spam"

    def test_in_operator(self):
        """Test parsing in rule with pipe-separated values."""
        rule = parse_rule("X-Rspamd-Action in reject|add header|greylist")
        assert rule.header == "X-Rspamd-Action"
        assert rule.operator == Operator.IN
        assert rule.value == ["reject", "add header", "greylist"]

    def test_exists(self):
        """Test parsing exists rule."""
        rule = parse_rule("X-Ovh-Spam-Reason exists")
        assert rule.header == "X-Ovh-Spam-Reason"
        assert rule.operator == Operator.EXISTS
        assert rule.value is None

    def test_regex_extraction(self):
        """Test parsing rule with regex extraction."""
        rule = parse_rule("X-Microsoft-Antispam /BCL:(\\d+)/ >= 7")
        assert rule.header == "X-Microsoft-Antispam"
        assert rule.pattern is not None
        assert rule.pattern.pattern == "BCL:(\\d+)"
        assert rule.operator == Operator.GTE
        assert rule.value == 7

    def test_regex_extraction_float(self):
        """Test parsing rule with regex extraction and float value."""
        rule = parse_rule("X-Spam-Status /score=([\\d.]+)/ >= 5.0")
        assert rule.pattern.pattern == "score=([\\d.]+)"
        assert rule.value == 5.0

    def test_empty_rule_raises(self):
        """Test that empty rule raises RuleParseError."""
        with pytest.raises(RuleParseError):
            parse_rule("")

    def test_comment_raises(self):
        """Test that comment raises RuleParseError."""
        with pytest.raises(RuleParseError):
            parse_rule("# This is a comment")

    def test_missing_operator_raises(self):
        """Test that missing operator raises RuleParseError."""
        with pytest.raises(RuleParseError):
            parse_rule("X-Spam-Flag")

    def test_invalid_operator_raises(self):
        """Test that invalid operator raises RuleParseError."""
        with pytest.raises(RuleParseError):
            parse_rule("X-Spam-Flag INVALID YES")

    def test_missing_value_raises(self):
        """Test that missing value raises RuleParseError for non-exists ops."""
        with pytest.raises(RuleParseError):
            parse_rule("X-Spam-Flag ==")

    def test_invalid_numeric_raises(self):
        """Test that invalid numeric value raises RuleParseError."""
        with pytest.raises(RuleParseError):
            parse_rule("X-Score >= notanumber")


class TestCheckRule:
    """Tests for check_rule function."""

    def test_numeric_gte_match(self):
        """Test numeric >= matching."""
        rule = parse_rule("X-Score >= 5")
        assert check_rule(rule, {"X-Score": "5"}) is True
        assert check_rule(rule, {"X-Score": "6"}) is True
        assert check_rule(rule, {"X-Score": "4"}) is False

    def test_numeric_gt_match(self):
        """Test numeric > matching."""
        rule = parse_rule("X-Score > 5")
        assert check_rule(rule, {"X-Score": "6"}) is True
        assert check_rule(rule, {"X-Score": "5"}) is False

    def test_numeric_float_match(self):
        """Test numeric matching with floats."""
        rule = parse_rule("X-Score >= 3.5")
        assert check_rule(rule, {"X-Score": "4.0"}) is True
        assert check_rule(rule, {"X-Score": "3.5"}) is True
        assert check_rule(rule, {"X-Score": "3.0"}) is False

    def test_string_eq_match(self):
        """Test string == matching."""
        rule = parse_rule("X-Spam-Flag == YES")
        assert check_rule(rule, {"X-Spam-Flag": "YES"}) is True
        assert check_rule(rule, {"X-Spam-Flag": "yes"}) is False
        assert check_rule(rule, {"X-Spam-Flag": "NO"}) is False

    def test_string_ne_match(self):
        """Test string != matching."""
        rule = parse_rule("X-Spam-Flag != YES")
        assert check_rule(rule, {"X-Spam-Flag": "NO"}) is True
        assert check_rule(rule, {"X-Spam-Flag": "YES"}) is False

    def test_prefix_match(self):
        """Test prefix matching."""
        rule = parse_rule("X-Spam-Status prefix Yes")
        assert check_rule(rule, {"X-Spam-Status": "Yes, score=5.2"}) is True
        assert check_rule(rule, {"X-Spam-Status": "No, score=1.0"}) is False

    def test_suffix_match(self):
        """Test suffix matching."""
        rule = parse_rule("X-Result suffix spam")
        assert check_rule(rule, {"X-Result": "result: spam"}) is True
        assert check_rule(rule, {"X-Result": "result: ham"}) is False

    def test_contains_match(self):
        """Test contains matching."""
        rule = parse_rule("X-Details contains spam")
        assert check_rule(rule, {"X-Details": "rule=spam, score=5"}) is True
        assert check_rule(rule, {"X-Details": "rule=ham, score=1"}) is False

    def test_in_match(self):
        """Test in operator matching."""
        rule = parse_rule("X-Action in reject|greylist")
        assert check_rule(rule, {"X-Action": "reject"}) is True
        assert check_rule(rule, {"X-Action": "greylist"}) is True
        assert check_rule(rule, {"X-Action": "accept"}) is False

    def test_exists_match(self):
        """Test exists matching."""
        rule = parse_rule("X-Spam-Reason exists")
        assert check_rule(rule, {"X-Spam-Reason": "bulk mail"}) is True
        assert check_rule(rule, {"X-Other-Header": "value"}) is False

    def test_regex_extraction_match(self):
        """Test regex extraction and matching."""
        rule = parse_rule("X-Microsoft-Antispam /BCL:(\\d+)/ >= 7")
        assert check_rule(rule, {"X-Microsoft-Antispam": "BCL:8;ARA:123"}) is True
        assert check_rule(rule, {"X-Microsoft-Antispam": "BCL:7;ARA:456"}) is True
        assert check_rule(rule, {"X-Microsoft-Antispam": "BCL:3;ARA:789"}) is False

    def test_regex_extraction_no_match(self):
        """Test regex extraction when pattern doesn't match."""
        rule = parse_rule("X-Microsoft-Antispam /BCL:(\\d+)/ >= 7")
        assert check_rule(rule, {"X-Microsoft-Antispam": "ARA:123"}) is False

    def test_case_insensitive_header_lookup(self):
        """Test that header lookup is case-insensitive."""
        rule = parse_rule("X-Spam-Flag == YES")
        assert check_rule(rule, {"x-spam-flag": "YES"}) is True
        assert check_rule(rule, {"X-SPAM-FLAG": "YES"}) is True

    def test_missing_header_returns_false(self):
        """Test that missing header returns False (except exists)."""
        rule = parse_rule("X-Spam-Flag == YES")
        assert check_rule(rule, {"X-Other": "value"}) is False

    def test_non_numeric_value_for_numeric_op(self):
        """Test that non-numeric header value returns False for numeric ops."""
        rule = parse_rule("X-Score >= 5")
        assert check_rule(rule, {"X-Score": "notanumber"}) is False


class TestIsSpam:
    """Tests for is_spam function."""

    def test_matches_first_rule(self):
        """Test that matching first rule returns spam."""
        rules = parse_rules([
            "X-Score >= 5",
            "X-Spam-Flag == YES",
        ])
        result, reason = is_spam({"X-Score": "6"}, rules)
        assert result is True
        assert "X-Score" in reason

    def test_matches_second_rule(self):
        """Test that matching second rule returns spam."""
        rules = parse_rules([
            "X-Score >= 5",
            "X-Spam-Flag == YES",
        ])
        result, reason = is_spam({"X-Score": "1", "X-Spam-Flag": "YES"}, rules)
        assert result is True
        assert "X-Spam-Flag" in reason

    def test_no_match(self):
        """Test that no match returns not spam."""
        rules = parse_rules([
            "X-Score >= 5",
            "X-Spam-Flag == YES",
        ])
        result, reason = is_spam({"X-Score": "1", "X-Spam-Flag": "NO"}, rules)
        assert result is False
        assert reason is None

    def test_empty_rules(self):
        """Test that empty rules returns not spam."""
        result, reason = is_spam({"X-Score": "10"}, [])
        assert result is False
        assert reason is None

    def test_empty_headers(self):
        """Test that empty headers returns not spam."""
        rules = parse_rules(["X-Score >= 5"])
        result, reason = is_spam({}, rules)
        assert result is False


class TestParseRules:
    """Tests for parse_rules function."""

    def test_parses_multiple_rules(self):
        """Test parsing multiple rules."""
        rules = parse_rules([
            "X-Score >= 5",
            "X-Spam-Flag == YES",
        ])
        assert len(rules) == 2

    def test_skips_empty_lines(self):
        """Test that empty lines are skipped."""
        rules = parse_rules([
            "X-Score >= 5",
            "",
            "X-Spam-Flag == YES",
        ])
        assert len(rules) == 2

    def test_skips_comments(self):
        """Test that comments are skipped."""
        rules = parse_rules([
            "# This is a comment",
            "X-Score >= 5",
            "# Another comment",
            "X-Spam-Flag == YES",
        ])
        assert len(rules) == 2

    def test_skips_invalid_rules(self):
        """Test that invalid rules are skipped."""
        rules = parse_rules([
            "X-Score >= 5",
            "Invalid rule without operator",
            "X-Spam-Flag == YES",
        ])
        # The invalid rule is silently skipped
        assert len(rules) == 2


class TestSpamRuleStr:
    """Tests for SpamRule __str__ method."""

    def test_simple_rule_str(self):
        """Test string representation of simple rule."""
        rule = parse_rule("X-Score >= 5")
        assert str(rule) == "X-Score >= 5"

    def test_string_rule_str(self):
        """Test string representation of string rule."""
        rule = parse_rule("X-Spam-Flag == YES")
        assert str(rule) == "X-Spam-Flag == YES"

    def test_in_rule_str(self):
        """Test string representation of in rule."""
        rule = parse_rule("X-Action in reject|greylist")
        assert str(rule) == "X-Action in reject|greylist"

    def test_exists_rule_str(self):
        """Test string representation of exists rule."""
        rule = parse_rule("X-Reason exists")
        assert str(rule) == "X-Reason exists"

    def test_regex_rule_str(self):
        """Test string representation of regex rule."""
        rule = parse_rule("X-Antispam /BCL:(\\d+)/ >= 7")
        assert "/BCL:(\\d+)/" in str(rule)
        assert ">= 7" in str(rule)
