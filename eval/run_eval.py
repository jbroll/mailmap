#!/usr/bin/env python3
"""Email classification evaluation: Claude vs Ollama"""

import mailbox
import json
import re
import os
import requests
import html
from email.header import decode_header, make_header
from pathlib import Path

MBOX_PATH = os.path.expanduser(
    "~/.thunderbird/dnnpv1xc.default-default/ImapMail/imap.purelymail.com/INBOX"
)
CATEGORIES_FILE = "/home/john/src/mailmap/categories.txt"
PROMPT_TEMPLATE_FILE = "/home/john/src/mailmap/mailmap/prompts/classify_email.txt"
OLLAMA_URL = "http://192.168.1.169:11434/api/generate"
OLLAMA_MODEL = "qwen3:14b"
OUTPUT_FILE = "/home/john/src/mailmap/eval/inbox_eval_001.json"
NUM_EMAILS = 25


def decode_header_value(value):
    """Decode an email header value properly."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def strip_html(text):
    """Strip HTML tags from text."""
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = html.unescape(text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_body(msg, max_chars=500):
    """Extract body text from email message."""
    body = ""
    if msg.is_multipart():
        # First try to find text/plain
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and part.get_content_disposition() != 'attachment':
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(charset, errors='replace')
                        break
                except Exception:
                    pass
        # Fall back to text/html
        if not body:
            for part in msg.walk():
                if part.get_content_type() == 'text/html' and part.get_content_disposition() != 'attachment':
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = strip_html(payload.decode(charset, errors='replace'))
                            break
                    except Exception:
                        pass
    else:
        try:
            charset = msg.get_content_charset() or 'utf-8'
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(charset, errors='replace')
                if msg.get_content_type() == 'text/html':
                    body = strip_html(body)
        except Exception:
            body = str(msg.get_payload())

    return body[:max_chars]


def parse_categories(categories_file):
    """Parse categories.txt into folders_text string and a dict of {name: description}."""
    categories = {}
    current_name = None
    current_desc_lines = []

    with open(categories_file, 'r') as f:
        for line in f:
            # Skip comments and blank lines
            if line.startswith('#') or not line.strip():
                if current_name and current_desc_lines:
                    # Save previous category
                    categories[current_name] = ' '.join(current_desc_lines).strip()
                    current_name = None
                    current_desc_lines = []
                continue

            # Check if this starts a new category (CategoryName: description on same line)
            match = re.match(r'^([A-Za-z][A-Za-z0-9]*): (.+)$', line.strip())
            if match:
                # Save previous category if any
                if current_name and current_desc_lines:
                    categories[current_name] = ' '.join(current_desc_lines).strip()
                current_name = match.group(1)
                current_desc_lines = [match.group(2).strip()]
            elif current_name and line.strip():
                # Continuation of current category description
                current_desc_lines.append(line.strip())

    # Save last category
    if current_name and current_desc_lines:
        categories[current_name] = ' '.join(current_desc_lines).strip()

    # Build folders_text
    folders_text = ""
    for name, desc in categories.items():
        folders_text += f"- {name}: {desc}\n"

    return categories, folders_text


def classify_with_ollama(prompt, timeout=120):
    """Send prompt to Ollama and parse the response."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        response_text = data.get("response", "")

        # Find JSON object in response
        json_match = re.search(r'\{[^{}]*"predicted_folder"[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                return {
                    "predicted_folder": parsed.get("predicted_folder", "Unknown"),
                    "secondary_labels": parsed.get("secondary_labels", []),
                    "confidence": float(parsed.get("confidence", 0.0)),
                    "raw_response": response_text[:300]
                }
            except json.JSONDecodeError:
                pass

        return {
            "predicted_folder": "PARSE_ERROR",
            "secondary_labels": [],
            "confidence": 0.0,
            "raw_response": response_text[:300]
        }
    except requests.Timeout:
        return {
            "predicted_folder": "ERROR",
            "secondary_labels": [],
            "confidence": 0.0,
            "raw_response": "TIMEOUT"
        }
    except Exception as e:
        return {
            "predicted_folder": "ERROR",
            "secondary_labels": [],
            "confidence": 0.0,
            "raw_response": str(e)[:300]
        }


# Claude's classification logic based on category descriptions
# This encodes the category rules as described in categories.txt
def classify_with_claude(from_addr, subject, body):
    """Classify email based on the category descriptions."""
    text = f"{from_addr} {subject} {body}".lower()
    subject_lower = subject.lower()
    from_lower = from_addr.lower()

    # Priority 1: Personal - individual person writing directly
    # Look for signals of a real person (not domain@service, not noreply, etc.)
    is_org_sender = any(kw in from_lower for kw in [
        'noreply', 'no-reply', 'donotreply', 'notifications', 'mailer', 'newsletter',
        'info@', 'hello@', 'support@', 'team@', 'mail@', 'news@', 'billing@',
        'alert@', 'account@', 'service@', 'admin@', 'updates@', 'marketing@',
        'orders@', 'receipts@', 'confirm@', 'do-not-reply', 'automated'
    ])

    # GitHub signals
    if any(kw in text for kw in ['github.com', 'pull request', 'issue opened', 'ci/cd', 'workflow run',
                                   'dependabot', 'github actions', 'pushed to', 'merged', 'commit']):
        if 'github' in from_lower or 'github' in subject_lower:
            return ("GitHub", 0.92, "Email is about GitHub repository activity such as PRs, issues, or CI results.")

    # AccountSecurity - 2FA, login alerts, password resets
    if any(kw in text for kw in ['verification code', 'two-factor', '2fa', 'one-time code',
                                   'otp', 'login attempt', 'password reset', 'suspicious activity',
                                   'security alert', 'sign-in attempt', 'confirm your email',
                                   'verify your', 'new sign-in', 'access code']):
        return ("AccountSecurity", 0.93, "Email contains a security code, login alert, or password reset request.")

    # Travel
    if any(kw in text for kw in ['flight', 'booking confirmation', 'reservation', 'hotel',
                                   'check-in', 'check in', 'itinerary', 'boarding pass',
                                   'airline', 'airbnb', 'vrbo', 'rental car', 'your trip']):
        return ("Travel", 0.88, "Email is a travel booking confirmation or trip update.")

    # Orders - fulfillment, shipping, tracking
    if any(kw in text for kw in ['your order', 'order #', 'order number', 'shipped', 'shipping confirmation',
                                   'tracking number', 'out for delivery', 'delivered', 'package',
                                   'dispatched', 'order status', 'order confirmation']):
        return ("Orders", 0.88, "Email is about an order being fulfilled or shipped.")

    # Receipts - payment confirmed
    if any(kw in text for kw in ['receipt', 'invoice', 'payment received', 'payment confirmed',
                                   'transaction', 'charge of', 'amount due', 'paid', 'billing statement',
                                   'your payment', 'subscription charge']):
        return ("Receipts", 0.85, "Email confirms a payment or presents an invoice.")

    # Healthcare
    if any(kw in text for kw in ['appointment', 'prescription', 'medication', 'doctor', 'clinic',
                                   'hospital', 'lab result', 'test result', 'health plan', 'insurance claim',
                                   'pharmacy', 'medical', 'patient', 'diagnosis', 'referral', 'copay']):
        return ("Healthcare", 0.87, "Email is from a medical provider or health insurer about care or coverage.")

    # Financial
    if any(kw in text for kw in ['account statement', 'bank statement', 'investment', 'portfolio',
                                   'dividend', 'tax return', 'brokerage', '401k', 'ira', 'mutual fund',
                                   'account balance', 'wire transfer', 'ach transfer', 'loan statement']):
        return ("Financial", 0.85, "Email is from a financial institution about accounts or investments.")

    # ECCG - Electric City Community Grocery
    if any(kw in text for kw in ['eccg', 'electric city community grocery', 'co-op', 'cooperative grocery',
                                   'grocery co', 'coop development']):
        return ("ECCG", 0.90, "Email is about the Electric City Community Grocery cooperative project.")

    # Wellness - yoga, fitness, wellness studio
    if any(kw in text for kw in ['yoga', 'fitness class', 'studio', 'wellness', 'pilates', 'gym membership',
                                   'class schedule', 'personal trainer', 'workout']):
        return ("Wellness", 0.85, "Email is from a yoga studio or wellness service about classes or memberships.")

    # Community - local org announcements
    if any(kw in text for kw in ['neighborhood', 'association', 'community meeting', 'local event',
                                   'homeowners', 'hoa', 'civic', 'town hall', 'residents']):
        return ("Community", 0.82, "Email is from a local community organization about shared business or events.")

    # Events - specific gatherings
    if any(kw in text for kw in ['invitation', 'event', 'webinar', 'conference', 'meetup',
                                   'rsvp', 'join us', 'register now', 'upcoming event', 'you are invited']):
        return ("Events", 0.80, "Email is about a specific event or gathering with a date and time.")

    # Career
    if any(kw in text for kw in ['job opportunity', 'recruiting', 'recruiter', 'position', 'hiring',
                                   'linkedin', 'resume', 'interview', 'application status', 'job offer',
                                   'career opportunity', 'talent']):
        return ("Career", 0.85, "Email is about a job opportunity or recruiting outreach.")

    # GitHub (broader)
    if 'github' in text:
        return ("GitHub", 0.82, "Email relates to GitHub platform activity.")

    # LocalPolitics
    if any(kw in text for kw in ['campaign', 'election', 'vote', 'senator', 'representative', 'congress',
                                   'political', 'ballot', 'civic advocacy', 'donate to', 're-elect',
                                   'act now', 'legislat', 'mayor', 'governor']):
        return ("LocalPolitics", 0.82, "Email is from a political campaign or civic advocacy organization.")

    # SocialMedia
    if any(kw in text for kw in ['twitter', 'facebook', 'instagram', 'linkedin notification', 'mastodon',
                                   'mentioned you', 'liked your', 'commented on', 'followed you',
                                   'friend request', 'new message on', 'reddit']):
        return ("SocialMedia", 0.85, "Email is a social media platform notification about account activity.")

    # TechSupport
    if any(kw in text for kw in ['ticket #', 'support ticket', 'case number', 'your request',
                                   'we received your', 'support team', 'issue has been', 'resolved']):
        return ("TechSupport", 0.83, "Email is a response to a support ticket about a problem you reported.")

    # OnlineServices - platform account/billing/policy
    if any(kw in text for kw in ['subscription', 'your account', 'service update', 'terms of service',
                                   'privacy policy', 'plan renewal', 'billing cycle', 'account notice',
                                   'platform update', 'system maintenance']):
        return ("OnlineServices", 0.78, "Email is from an online service provider about account status or billing.")

    # RKRoll
    if any(kw in text for kw in ['rkroll.com', 'rkroll', 'r.k. roll']):
        return ("RKRoll", 0.88, "Email is business correspondence addressed to RKRoll.com as a company.")

    # Shopping - product discovery
    if any(kw in text for kw in ['new arrivals', 'shop now', 'browse', 'new collection', 'just launched',
                                   'back in stock', 'now available', 'product launch']):
        return ("Shopping", 0.78, "Email is from a retailer showing products to consider buying.")

    # Promotions - sales, discounts, surveys
    if any(kw in text for kw in ['sale', 'discount', 'promo', 'offer', 'deal', 'coupon', '%  off',
                                   'limited time', 'feedback', 'survey', 'review us', 'rate your',
                                   'special offer', 'reward']):
        return ("Promotions", 0.78, "Email offers a discount or requests feedback/review.")

    # Newsletters - subscribed content
    if any(kw in text for kw in ['unsubscribe', 'newsletter', 'weekly digest', 'monthly update',
                                   'edition', 'roundup', 'digest', 'issue #']):
        return ("Newsletters", 0.75, "Email is a subscribed newsletter delivering ongoing content.")

    # Default: if not clearly an org sender, might be Personal
    if not is_org_sender:
        return ("Personal", 0.60, "Email appears to be from an individual person sending direct correspondence.")

    # Fallback
    return ("Newsletters", 0.50, "Email doesn't match specific categories; treated as general newsletter or update.")


def main():
    print("Loading categories...")
    categories, folders_text = parse_categories(CATEGORIES_FILE)
    print(f"  Loaded {len(categories)} categories")

    print("Loading prompt template...")
    with open(PROMPT_TEMPLATE_FILE, 'r') as f:
        prompt_template = f.read()

    print(f"Opening mbox: {MBOX_PATH}")
    mbox = mailbox.mbox(MBOX_PATH)
    messages = list(mbox)
    print(f"  Total messages in INBOX: {len(messages)}")
    messages = messages[:NUM_EMAILS]
    print(f"  Processing first {len(messages)} messages")

    results = []

    for idx, msg in enumerate(messages):
        print(f"\n[{idx+1:2d}/{NUM_EMAILS}] Processing email...")

        # Extract fields
        message_id = decode_header_value(msg.get('Message-ID', f'<unknown-{idx}>'))
        from_addr = decode_header_value(msg.get('From', ''))
        subject = decode_header_value(msg.get('Subject', '(no subject)'))
        date = decode_header_value(msg.get('Date', ''))
        body = extract_body(msg, max_chars=500)

        print(f"         From: {from_addr[:50]}")
        print(f"      Subject: {subject[:60]}")

        # Claude classification
        claude_folder, claude_conf, claude_reason = classify_with_claude(
            from_addr, subject, body
        )
        print(f"       Claude: {claude_folder} ({claude_conf:.2f})")

        # Build Ollama prompt
        prompt = prompt_template.format(
            folders_text=folders_text,
            from_addr=from_addr,
            subject=subject,
            body=body,
            attachments_section=""
        )

        # Ollama classification
        print(f"       Ollama: calling API...", end='', flush=True)
        ollama_result = classify_with_ollama(prompt, timeout=120)
        print(f" {ollama_result['predicted_folder']} ({ollama_result['confidence']:.2f})")

        agreement = (claude_folder == ollama_result['predicted_folder'])

        entry = {
            "index": idx,
            "message_id": message_id.strip(),
            "date": date,
            "from_addr": from_addr,
            "subject": subject,
            "body_preview": body[:150],
            "claude": {
                "predicted_folder": claude_folder,
                "confidence": claude_conf,
                "reason": claude_reason
            },
            "ollama": {
                "predicted_folder": ollama_result['predicted_folder'],
                "secondary_labels": ollama_result['secondary_labels'],
                "confidence": ollama_result['confidence'],
                "raw_response": ollama_result['raw_response']
            },
            "agreement": agreement
        }
        results.append(entry)

    # Save results
    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} results to {OUTPUT_FILE}")

    # Print summary table
    print("\n" + "="*110)
    print(f"{'#':>3}  {'From':30}  {'Subject':40}  {'Claude':18}  {'Ollama':18}  {'Agree':5}")
    print("-"*110)
    agree_count = 0
    for r in results:
        frm = r['from_addr'][:30]
        subj = r['subject'][:40]
        claude_f = r['claude']['predicted_folder'][:18]
        ollama_f = r['ollama']['predicted_folder'][:18]
        agree = "YES" if r['agreement'] else "no"
        if r['agreement']:
            agree_count += 1
        print(f"{r['index']:>3}  {frm:30}  {subj:40}  {claude_f:18}  {ollama_f:18}  {agree:5}")

    print("="*110)
    print(f"Agreement rate: {agree_count}/{NUM_EMAILS} ({100*agree_count/NUM_EMAILS:.0f}%)")


if __name__ == '__main__':
    main()
