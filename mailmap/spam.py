r"""Spam detection from email headers.

Parses spam rules in DSL format and checks email headers against them.

Rule format: HEADER [/REGEX/] OPERATOR VALUE

Examples:
    X-MS-Exchange-Organization-SCL >= 5
    X-Spam-Flag == YES
    X-Spam-Status prefix Yes
    X-Microsoft-Antispam /BCL:(\d+)/ >= 7
    X-Rspamd-Action in reject|add header|greylist
    X-Ovh-Spam-Reason exists
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Operator(Enum):
    """Comparison operators for spam rules."""
    GTE = ">="
    GT = ">"
    LTE = "<="
    LT = "<"
    EQ = "=="
    NE = "!="
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    IN = "in"
    EXISTS = "exists"


@dataclass
class SpamRule:
    """A parsed spam detection rule."""
    header: str
    operator: Operator
    value: Any = None  # number, string, or list
    pattern: re.Pattern | None = None  # regex for extraction

    def __str__(self) -> str:
        parts = [self.header]
        if self.pattern:
            parts.append(f"/{self.pattern.pattern}/")
        parts.append(self.operator.value)
        if self.value is not None:
            if isinstance(self.value, list):
                parts.append("|".join(self.value))
            else:
                parts.append(str(self.value))
        return " ".join(parts)


class RuleParseError(Exception):
    """Error parsing a spam rule."""
    pass


def parse_rule(rule: str) -> SpamRule:
    """Parse a spam rule DSL string into a SpamRule object.

    Format: HEADER [/REGEX/] OPERATOR [VALUE]

    Args:
        rule: The rule string to parse

    Returns:
        SpamRule object

    Raises:
        RuleParseError: If the rule cannot be parsed
    """
    rule = rule.strip()
    if not rule or rule.startswith("#"):
        raise RuleParseError("Empty or comment rule")

    # Split into tokens, preserving regex patterns
    tokens = _tokenize(rule)
    if not tokens:
        raise RuleParseError(f"No tokens in rule: {rule}")

    # First token is always the header
    header = tokens[0]
    tokens = tokens[1:]

    # Check for regex pattern
    pattern = None
    if tokens and tokens[0].startswith("/") and tokens[0].endswith("/"):
        pattern_str = tokens[0][1:-1]  # Strip slashes
        try:
            pattern = re.compile(pattern_str)
        except re.error as e:
            raise RuleParseError(f"Invalid regex pattern: {e}")
        tokens = tokens[1:]

    # Next token is the operator
    if not tokens:
        raise RuleParseError(f"Missing operator in rule: {rule}")

    op_str = tokens[0].lower()
    tokens = tokens[1:]

    try:
        operator = _parse_operator(op_str)
    except ValueError:
        raise RuleParseError(f"Unknown operator '{op_str}' in rule: {rule}")

    # Parse value based on operator
    value = None
    if operator == Operator.EXISTS:
        # No value needed
        pass
    elif operator == Operator.IN:
        # Rest of tokens joined and split by |
        if not tokens:
            raise RuleParseError(f"Missing value for 'in' operator: {rule}")
        value_str = " ".join(tokens)
        value = [v.strip() for v in value_str.split("|")]
    elif operator in (Operator.GTE, Operator.GT, Operator.LTE, Operator.LT):
        # Numeric value
        if not tokens:
            raise RuleParseError(f"Missing numeric value: {rule}")
        try:
            value_str = tokens[0]
            value = float(value_str) if "." in value_str else int(value_str)
        except ValueError:
            raise RuleParseError(f"Invalid numeric value '{tokens[0]}': {rule}")
    else:
        # String value (rest of tokens)
        if not tokens:
            raise RuleParseError(f"Missing string value: {rule}")
        value = " ".join(tokens)

    return SpamRule(header=header, operator=operator, value=value, pattern=pattern)


def _tokenize(rule: str) -> list[str]:
    """Tokenize a rule string, keeping regex patterns intact."""
    tokens = []
    i = 0
    current = ""

    while i < len(rule):
        char = rule[i]

        if char == "/":
            # Start of regex pattern - find closing /
            if current:
                tokens.append(current)
                current = ""
            end = rule.find("/", i + 1)
            if end == -1:
                raise RuleParseError(f"Unclosed regex pattern in: {rule}")
            tokens.append(rule[i:end + 1])
            i = end + 1
        elif char.isspace():
            if current:
                tokens.append(current)
                current = ""
            i += 1
        else:
            current += char
            i += 1

    if current:
        tokens.append(current)

    return tokens


def _parse_operator(op_str: str) -> Operator:
    """Parse operator string to Operator enum."""
    op_map = {
        ">=": Operator.GTE,
        ">": Operator.GT,
        "<=": Operator.LTE,
        "<": Operator.LT,
        "==": Operator.EQ,
        "!=": Operator.NE,
        "prefix": Operator.PREFIX,
        "suffix": Operator.SUFFIX,
        "contains": Operator.CONTAINS,
        "in": Operator.IN,
        "exists": Operator.EXISTS,
    }
    if op_str not in op_map:
        raise ValueError(f"Unknown operator: {op_str}")
    return op_map[op_str]


def check_rule(rule: SpamRule, headers: dict[str, str]) -> bool:
    """Check if headers match a spam rule.

    Args:
        rule: The spam rule to check
        headers: Dict of header name -> value (case-insensitive keys)

    Returns:
        True if the rule matches (email is spam), False otherwise
    """
    # Case-insensitive header lookup
    headers_lower = {k.lower(): v for k, v in headers.items()}
    header_value = headers_lower.get(rule.header.lower())

    # EXISTS operator
    if rule.operator == Operator.EXISTS:
        return header_value is not None

    # No header = no match (except EXISTS)
    if header_value is None:
        return False

    # Extract value if regex pattern specified
    if rule.pattern:
        match = rule.pattern.search(header_value)
        if not match:
            return False
        # Use first capture group, or whole match if no groups
        header_value = match.group(1) if match.groups() else match.group(0)

    # Numeric comparisons
    if rule.operator in (Operator.GTE, Operator.GT, Operator.LTE, Operator.LT):
        try:
            num_value = float(header_value)
        except (ValueError, TypeError):
            return False

        if rule.operator == Operator.GTE:
            return num_value >= rule.value
        elif rule.operator == Operator.GT:
            return num_value > rule.value
        elif rule.operator == Operator.LTE:
            return num_value <= rule.value
        elif rule.operator == Operator.LT:
            return num_value < rule.value

    # String comparisons
    if rule.operator == Operator.EQ:
        return header_value == rule.value
    elif rule.operator == Operator.NE:
        return header_value != rule.value
    elif rule.operator == Operator.PREFIX:
        return header_value.startswith(rule.value)
    elif rule.operator == Operator.SUFFIX:
        return header_value.endswith(rule.value)
    elif rule.operator == Operator.CONTAINS:
        return rule.value in header_value
    elif rule.operator == Operator.IN:
        return header_value in rule.value

    return False


def is_spam(headers: dict[str, str], rules: list[SpamRule]) -> tuple[bool, str | None]:
    """Check if email headers indicate spam.

    Args:
        headers: Dict of header name -> value
        rules: List of spam rules to check

    Returns:
        Tuple of (is_spam, matching_rule_str or None)
    """
    for rule in rules:
        if check_rule(rule, headers):
            return True, str(rule)
    return False, None


def parse_rules(rule_strings: list[str]) -> list[SpamRule]:
    """Parse a list of rule strings into SpamRule objects.

    Skips empty lines and comments (starting with #).

    Args:
        rule_strings: List of rule strings

    Returns:
        List of SpamRule objects
    """
    rules = []
    for rule_str in rule_strings:
        rule_str = rule_str.strip()
        if not rule_str or rule_str.startswith("#"):
            continue
        try:
            rules.append(parse_rule(rule_str))
        except RuleParseError:
            # Log warning but continue
            pass
    return rules
