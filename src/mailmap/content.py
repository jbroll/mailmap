"""Email content cleaning and normalization utilities."""

import re


def clean_email_content(body: str, max_length: int = 500) -> str:
    """
    Clean email content to reduce noise before sending to LLM.

    Removes:
    - HTML tags
    - Email signatures
    - Reply chains (quoted text)
    - Excessive whitespace
    - Common disclaimers
    - URLs (replaced with [URL])
    """
    if not body:
        return ""

    text = body

    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)

    # Remove URLs (keep note that there was one)
    text = re.sub(r'https?://\S+', '[URL]', text)

    # Remove quoted reply chains (lines starting with >)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip quoted lines
        if stripped.startswith('>'):
            continue
        # Skip "On ... wrote:" lines
        if re.match(r'^On .+ wrote:?\s*$', stripped, re.IGNORECASE):
            continue
        # Skip "From: ... Sent: ... To: ... Subject:" header blocks
        if re.match(r'^(From|Sent|To|Subject|Date|Cc|Bcc):\s', stripped):
            continue
        cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)

    # Remove signature blocks (after -- or common signature markers)
    signature_markers = [
        r'\n--\s*\n',  # Standard -- signature delimiter
        r'\nSent from my ',  # Mobile signatures
        r'\nGet Outlook for ',
        r'\n_{3,}\n',  # ___ dividers
        r'\nThis email and any ',  # Disclaimers
        r'\nConfidentiality Notice',
        r'\nThis message contains ',
        r'\nIf you are not the intended ',
    ]

    for marker in signature_markers:
        match = re.search(marker, text, re.IGNORECASE)
        if match:
            text = text[:match.start()]

    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces/tabs to single space
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Multiple blank lines to double
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
    """
    Extract a clean summary of an email for LLM analysis.

    Returns a dict with cleaned subject, from, and body.
    """
    # Clean subject - remove Re:, Fwd:, etc. (may be multiple)
    clean_subject = subject or ""
    while True:
        new_subject = re.sub(r'^(Re|Fwd|Fw):\s*', '', clean_subject, flags=re.IGNORECASE)
        if new_subject == clean_subject:
            break
        clean_subject = new_subject
    clean_subject = clean_subject.strip()

    # Extract just the name/address from from_addr
    clean_from = from_addr or ""
    # Remove angle brackets if present: "Name <email>" -> "Name"
    name_match = re.match(r'^([^<]+)<[^>]+>$', clean_from)
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
