"""Email content cleaning and normalization utilities."""

import re

# Pre-compiled regex patterns for better performance
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_HTML_ENTITY_RE = re.compile(r'&[a-zA-Z]+;')
_HTML_NUMERIC_RE = re.compile(r'&#\d+;')
_URL_RE = re.compile(r'https?://\S+')
_ON_WROTE_RE = re.compile(r'^On .+ wrote:?\s*$', re.IGNORECASE)
_EMAIL_HEADER_RE = re.compile(r'^(From|Sent|To|Subject|Date|Cc|Bcc):\s')
_MULTI_SPACE_RE = re.compile(r'[ \t]+')
_MULTI_NEWLINE_RE = re.compile(r'\n\s*\n\s*\n+')
_REPLY_PREFIX_RE = re.compile(r'^(Re|Fwd|Fw):\s*', re.IGNORECASE)
_FROM_NAME_RE = re.compile(r'^([^<]+)<[^>]+>$')

# Signature marker patterns (pre-compiled)
_SIGNATURE_PATTERNS = [
    re.compile(r'\n--\s*\n'),  # Standard -- signature delimiter
    re.compile(r'\nSent from my ', re.IGNORECASE),  # Mobile signatures
    re.compile(r'\nGet Outlook for ', re.IGNORECASE),
    re.compile(r'\n_{3,}\n'),  # ___ dividers
    re.compile(r'\nThis email and any ', re.IGNORECASE),  # Disclaimers
    re.compile(r'\nConfidentiality Notice', re.IGNORECASE),
    re.compile(r'\nThis message contains ', re.IGNORECASE),
    re.compile(r'\nIf you are not the intended ', re.IGNORECASE),
]


def clean_email_content(body: str, max_length: int = 500) -> str:
    """Clean email content to reduce noise before sending to LLM.

    Removes:
    - HTML tags
    - Email signatures
    - Reply chains (quoted text)
    - Excessive whitespace
    - Common disclaimers
    - URLs (replaced with [URL])

    Args:
        body: Raw email body text
        max_length: Maximum length of cleaned content

    Returns:
        Cleaned email content string
    """
    if not body:
        return ""

    text = body

    # Remove HTML tags
    text = _HTML_TAG_RE.sub(' ', text)

    # Remove HTML entities
    text = _HTML_ENTITY_RE.sub(' ', text)
    text = _HTML_NUMERIC_RE.sub(' ', text)

    # Remove URLs (keep note that there was one)
    text = _URL_RE.sub('[URL]', text)

    # Remove quoted reply chains (lines starting with >)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip quoted lines
        if stripped.startswith('>'):
            continue
        # Skip "On ... wrote:" lines
        if _ON_WROTE_RE.match(stripped):
            continue
        # Skip "From: ... Sent: ... To: ... Subject:" header blocks
        if _EMAIL_HEADER_RE.match(stripped):
            continue
        cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)

    # Remove signature blocks (after -- or common signature markers)
    for pattern in _SIGNATURE_PATTERNS:
        match = pattern.search(text)
        if match:
            text = text[:match.start()]

    # Normalize whitespace
    text = _MULTI_SPACE_RE.sub(' ', text)  # Multiple spaces/tabs to single space
    text = _MULTI_NEWLINE_RE.sub('\n\n', text)  # Multiple blank lines to double
    text = text.strip()

    # Truncate to max length, trying to break at sentence/word boundary
    if len(text) > max_length:
        text = text[:max_length]
        # Try to break at last sentence
        last_period = text.rfind('. ')
        if last_period > max_length * 0.5:
            text = text[:last_period + 1]
        else:
            # Break at last word
            last_space = text.rfind(' ')
            if last_space > max_length * 0.7:
                text = text[:last_space] + '...'

    return text


def extract_email_summary(
    subject: str,
    from_addr: str,
    body: str,
    max_body_length: int = 300
) -> dict[str, str]:
    """Extract a clean summary of an email for LLM analysis.

    Args:
        subject: Email subject line
        from_addr: From address (may include name)
        body: Email body text
        max_body_length: Maximum body preview length

    Returns:
        Dict with cleaned subject, from_addr, and body
    """
    # Clean subject - remove Re:, Fwd:, etc. (may be multiple)
    clean_subject = subject or ""
    while True:
        new_subject = _REPLY_PREFIX_RE.sub('', clean_subject)
        if new_subject == clean_subject:
            break
        clean_subject = new_subject
    clean_subject = clean_subject.strip()

    # Extract just the name/address from from_addr
    clean_from = from_addr or ""
    # Remove angle brackets if present: "Name <email>" -> "Name"
    name_match = _FROM_NAME_RE.match(clean_from)
    if name_match:
        clean_from = name_match.group(1).strip()
    clean_from = clean_from.strip('"\'')

    # Clean body
    clean_body = clean_email_content(body, max_body_length)

    return {
        "subject": clean_subject,
        "from_addr": clean_from,
        "body": clean_body,
    }
