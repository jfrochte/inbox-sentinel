"""
contacts.py -- Contact knowledge base: one vCard 3.0 file per email address.

Dependencies within the package:
  - utils (log)
  - vcard (read_vcard, write_vcard)

Leaf module, only imported by main.py.
Contact files are stored in contacts/ (one .vcf per address).

Main functions:
  - load_contact / save_contact: load/save vCard
  - format_contact_for_prompt: format contact as prompt fragment
  - build_contact_card: IMAP material -> rule-based + LLM -> vCard dict
"""

# ============================================================
# External dependencies
# ============================================================
import copy
import os
import re
import uuid
from datetime import datetime, timezone

import requests

# ============================================================
# Internal package imports
# ============================================================
from email_report.utils import log
from email_report.vcard import read_vcard, write_vcard
from email_report.llm_profiles import profile_to_options


# ============================================================
# Constants
# ============================================================
_CONTACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contacts")

_PRODID = "-//Inbox Sentinel//EN"

# Values ignored during merge (LLM placeholders for "unknown")
_SKIP_VALUES = frozenset({
    "nicht bestimmbar", "nicht beurteilbar", "unbekannt",
    "keine neuen informationen", "n/a", "-", "\u2014", "",
    "not determinable", "unknown", "not assessable",
    "no new information",
})

# Regex for phone numbers in signatures
_TEL_LINE_RE = re.compile(
    r"(?:Tel\.?|Fon|Phone|Mobil|Fax|Telefon|Handy)\s*[:.]?\s*"
    r"([\+\d][\d\s/\-().]{6,})",
    re.IGNORECASE,
)
_TEL_INTL_RE = re.compile(r"\+\d[\d\s/\-().]{7,}")

# Regex for URLs in signatures (no mailto:, no tracking pixels)
_SIG_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRACKING_URL_RE = re.compile(r"(?i)(?:track|pixel|click|open|beacon|unsubscribe|list-unsubscribe)")


# ============================================================
# Helper functions
# ============================================================
def _email_to_filename(email_addr: str) -> str:
    """Converts an email address to a filename."""
    return email_addr.strip().lower().replace("@", "_") + ".vcf"


def _clean_display_name(raw: str) -> str:
    """Extracts a clean name from IMAP header format like '"Mustermann, Max" <max@...>'."""
    if not raw:
        return ""
    m = re.match(r'"?([^"<]+)"?\s*<', raw)
    if m:
        return m.group(1).strip().strip('"')
    return raw.strip()


def _ensure_contacts_dir() -> str:
    """Creates the contacts directory if needed and returns the path."""
    os.makedirs(_CONTACTS_DIR, exist_ok=True)
    return _CONTACTS_DIR


def _split_name(display_name: str) -> dict:
    """
    Heuristic: split display name into family/given.
    Recognizes 'Last, First' and 'First Last' patterns.
    """
    name = display_name.strip()
    if not name:
        return {}
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return {"family": parts[0], "given": parts[1],
                    "additional": "", "prefix": "", "suffix": ""}
    parts = name.rsplit(None, 1)
    if len(parts) == 2:
        return {"family": parts[1], "given": parts[0],
                "additional": "", "prefix": "", "suffix": ""}
    return {"family": name, "given": "",
            "additional": "", "prefix": "", "suffix": ""}


def _is_skip_value(val: str) -> bool:
    """Checks whether a value is an LLM placeholder that should be ignored."""
    return (val or "").strip().lower().rstrip(".") in _SKIP_VALUES


# Common phone number formats (after sanitization):
#   +49 234 777 27 121    (international with spaces)
#   +49-234-777-27-121    (international with hyphens)
#   +49 (0) 234 777 27121 (international with zero-bracket)
#   0234 777 27 121       (national)
#   0234/77727121         (national with slash)
# Minimum 7 digits, maximum 15 (ITU-T E.164).
_TEL_VALID_RE = re.compile(
    r"^\+?\d[\d\s/\-()]{5,}$"
)


def _sanitize_tel(raw: str) -> str:
    """
    Sanitizes a phone number:
    - Strip trailing/leading junk (brackets, dots, commas, semicolons)
    - Repair or remove unbalanced parentheses
    - Validate: 7-15 digits, only allowed characters
    Returns empty string if not repairable.
    """
    s = raw.strip()
    if not s:
        return ""

    # Strip trailing junk (anything that's not a digit or closing bracket)
    s = re.sub(r'[.,;:\s]+$', '', s)
    # Trailing open bracket (e.g. "...121 (")
    s = re.sub(r'\s*\(\s*$', '', s)
    # Leading junk (everything before + or first digit)
    s = re.sub(r'^[^+\d]+', '', s)

    # Balance parentheses: only keep "(0)" or "(0xx)" patterns
    # Everything else: remove brackets
    balanced = []
    i = 0
    while i < len(s):
        if s[i] == '(':
            close = s.find(')', i)
            if close > i:
                inner = s[i+1:close]
                # Only keep if content looks like (0) or (0234)
                if re.match(r'^0\d{0,4}$', inner.strip()):
                    balanced.append(s[i:close+1])
                    i = close + 1
                    continue
                else:
                    # Remove brackets, keep content
                    balanced.append(inner)
                    i = close + 1
                    continue
            else:
                # No closing bracket: skip
                i += 1
                continue
        elif s[i] == ')':
            # Orphaned closing bracket: skip
            i += 1
            continue
        else:
            balanced.append(s[i])
            i += 1
    s = ''.join(balanced).strip()

    # Normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    # Count digits (E.164: min 7, max 15)
    digits = re.sub(r'\D', '', s)
    if len(digits) < 7 or len(digits) > 15:
        return ""

    # Only allowed characters?
    if not _TEL_VALID_RE.match(s):
        return ""

    return s


_EMAIL_VALID_RE = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def _sanitize_email(raw: str) -> str:
    """
    Sanitizes and validates an email address.
    Returns empty string if invalid.
    """
    s = raw.strip().lower()
    if not s:
        return ""

    # Strip trailing/leading junk
    s = s.strip('<>"\' ')

    if not _EMAIL_VALID_RE.match(s):
        return ""

    return s


# ============================================================
# Load / save
# ============================================================
def load_contact(email_addr: str) -> dict | None:
    """Loads a contact from vCard. Returns None if not found."""
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    data = read_vcard(path)
    if data is None:
        log.debug("Kein Kontakt fuer %s", email_addr)
    return data


def save_contact(email_addr: str, data: dict) -> None:
    """Saves a contact as vCard (0o600 permissions). Sets REV automatically."""
    _ensure_contacts_dir()
    data["REV"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not data.get("PRODID"):
        data["PRODID"] = _PRODID
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    write_vcard(path, data)


# ============================================================
# Rule-based extraction from email headers
# ============================================================
def extract_from_headers(email_dict: dict) -> dict:
    """
    Extracts contact info from email headers using rules.
    Returns a partial vCard dict (only populated fields).
    """
    result = {}

    # FN from From header
    from_raw = (email_dict.get("from") or "").strip()
    fn = _clean_display_name(from_raw)
    if fn:
        result["FN"] = fn
        n = _split_name(fn)
        if n:
            result["N"] = n

    # EMAIL from from_addr (with validation)
    email_addr = _sanitize_email((email_dict.get("from_addr") or ""))
    if email_addr:
        result["EMAIL"] = email_addr

    # TZ from Date header (extract timezone offset)
    date_str = (email_dict.get("date") or "").strip()
    if date_str:
        tz_match = re.search(r'([+-]\d{2}:?\d{2})\s*$', date_str)
        if tz_match:
            tz = tz_match.group(1)
            if len(tz) == 5 and ':' not in tz:
                tz = tz[:3] + ':' + tz[3:]
            result["TZ"] = tz

    return result


# ============================================================
# Rule-based extraction from signature
# ============================================================
def extract_from_signature(body: str) -> dict:
    """
    Extracts TEL and URL from the signature area (last ~15 lines).
    Returns a partial vCard dict.
    """
    result = {}
    if not body:
        return result

    lines = body.strip().splitlines()
    sig_lines = lines[-15:] if len(lines) > 15 else lines
    sig_text = "\n".join(sig_lines)

    # Phone numbers (with sanitizing)
    tels = []
    for line in sig_lines:
        m = _TEL_LINE_RE.search(line)
        if m:
            tel = _sanitize_tel(m.group(1))
            if tel and tel not in tels:
                tels.append(tel)
    # International numbers without label
    for m in _TEL_INTL_RE.finditer(sig_text):
        tel = _sanitize_tel(m.group(0))
        if tel and tel not in tels:
            tels.append(tel)
    if tels:
        result["TEL"] = tels

    # URLs (no mailto:, no tracking)
    urls = []
    for m in _SIG_URL_RE.finditer(sig_text):
        url = m.group(0).rstrip('.,;)>')
        if _TRACKING_URL_RE.search(url):
            continue
        if url not in urls:
            urls.append(url)
    if urls:
        result["URL"] = urls[0]  # Nur die erste sinnvolle URL

    return result


# ============================================================
# Prompt formatting
# ============================================================
def format_contact_for_prompt(contact_data: dict | None) -> str:
    """
    Formats a vCard contact as a prompt fragment.
    Returns empty string if None or no useful data.
    """
    if not contact_data:
        return ""

    fn = (contact_data.get("FN") or "").strip()
    email = (contact_data.get("EMAIL") or "").strip()
    if not fn and not email:
        return ""

    lines = ["--- SENDER CONTEXT ---"]

    ident = f"Known sender: {fn}" if fn else "Known sender"
    if email:
        ident += f" ({email})"
    lines.append(ident)

    # Map vCard fields directly
    field_map = [
        ("ORG", "Organization"),
        ("TITLE", "Title"),
        ("ROLE", "Role"),
        ("CATEGORIES", "Categories"),
    ]
    for key, label in field_map:
        val = (contact_data.get(key) or "").strip()
        if val and not _is_skip_value(val):
            lines.append(f"{label}: {val}")

    # NOTE: only LLM section (before ---\nUser:) as context
    note = (contact_data.get("NOTE") or "").strip()
    if note:
        user_sep = note.find("---\nUser:")
        llm_note = note[:user_sep].strip() if user_sep >= 0 else note.strip()
        if llm_note:
            lines.append(f"Profile notes:\n{llm_note}")

    lines.append("--- END SENDER CONTEXT ---")

    # Only return if more than frame + identity is present
    if len(lines) <= 3:
        return ""

    return "\n".join(lines) + "\n\n"


# ============================================================
# Contact merge
# ============================================================
_EMPTY_VCARD: dict = {
    "FN": "", "N": {}, "NICKNAME": "", "EMAIL": "",
    "TEL": [], "ADR": "", "ORG": "", "TITLE": "", "ROLE": "",
    "URL": "", "NOTE": "", "BDAY": "", "UID": "", "REV": "",
    "PRODID": _PRODID, "CATEGORIES": "", "TZ": "", "GEO": "",
    "SORT-STRING": "",
}


def merge_contact(existing: dict | None, header_info: dict,
                  sig_info: dict, llm_info: dict) -> dict:
    """
    Merges rule-based + LLM-extracted info into an existing contact.
    Priority: header_info > sig_info > llm_info (for overlapping fields).
    NOTE: LLM section is fully replaced, user section (---\\nUser:) is preserved.
    """
    if existing:
        contact = copy.deepcopy(existing)
    else:
        contact = copy.deepcopy(_EMPTY_VCARD)

    # Keep existing UID or generate new one
    if not contact.get("UID"):
        contact["UID"] = str(uuid.uuid4())

    # Merge fields: LLM first (lowest priority), then sig, then header (highest)
    # Simple string fields
    simple_fields = ["FN", "ORG", "TITLE", "ROLE", "NICKNAME",
                     "ADR", "URL", "BDAY", "CATEGORIES", "TZ", "GEO"]
    for source in [llm_info, sig_info, header_info]:
        for key in simple_fields:
            val = (source.get(key) or "").strip()
            if val and not _is_skip_value(val):
                contact[key] = val

    # EMAIL: separate handling with validation
    for source in [llm_info, sig_info, header_info]:
        raw_email = (source.get("EMAIL") or "").strip()
        if raw_email:
            clean = _sanitize_email(raw_email)
            if clean:
                contact["EMAIL"] = clean

    # N field (structured)
    for source in [llm_info, sig_info, header_info]:
        n = source.get("N")
        if n and isinstance(n, dict) and (n.get("family") or n.get("given")):
            contact["N"] = n

    # TEL: merge (union of lists, sanitize all)
    existing_tels = contact.get("TEL") or []
    if isinstance(existing_tels, str):
        existing_tels = [existing_tels] if existing_tels else []
    # Re-sanitize existing TELs (clean up old dirty entries)
    existing_tels = [t for t in (_sanitize_tel(t) for t in existing_tels) if t]
    for source in [sig_info, header_info]:
        new_tels = source.get("TEL") or []
        if isinstance(new_tels, str):
            new_tels = [new_tels]
        for t in new_tels:
            clean = _sanitize_tel(t)
            if clean and clean not in existing_tels:
                existing_tels.append(clean)
    contact["TEL"] = existing_tels

    # NOTE: fully replace LLM section, preserve user section
    new_note = (llm_info.get("NOTE") or "").strip()
    if new_note and not _is_skip_value(new_note):
        user_section = ""
        old_note = (contact.get("NOTE") or "").strip()
        if old_note:
            user_idx = old_note.find("---\nUser:")
            if user_idx >= 0:
                user_section = old_note[user_idx:]
        if user_section:
            contact["NOTE"] = new_note + "\n" + user_section
        else:
            contact["NOTE"] = new_note

    # Derive SORT-STRING from N.family
    n = contact.get("N")
    if isinstance(n, dict) and n.get("family"):
        contact["SORT-STRING"] = n["family"]

    return contact


# ============================================================
# LLM extraction + contact card builder
# ============================================================
_BEGIN_RE = re.compile(r"<<\s*BEGIN\s*>>", re.IGNORECASE)
_END_RE = re.compile(r"<<\s*END\s*>>", re.IGNORECASE)

# Single-line fields in the contact block
_CONTACT_LABELS = [
    ("ORG", re.compile(r"(?i)^ORG\s*[:=\-]\s*")),
    ("TITLE", re.compile(r"(?i)^TITLE\s*[:=\-]\s*")),
    ("ROLE", re.compile(r"(?i)^ROLE\s*[:=\-]\s*")),
    ("CATEGORIES", re.compile(r"(?i)^CATEGORIES\s*[:=\-]\s*")),
]

# NOTE is a multiline field (last before <<END>>)
_NOTE_RE = re.compile(r"(?i)^NOTE\s*[:=\-]\s*")


def _extract_response_text(data) -> str:
    """Extracts response text from Ollama/OpenAI-compatible response."""
    if not isinstance(data, dict):
        return ""
    text = (data.get("response") or "").strip()
    if not text:
        msg = data.get("message")
        if isinstance(msg, dict):
            text = (msg.get("content") or "").strip()
    if not text:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            ch0 = choices[0]
            if isinstance(ch0, dict):
                m = ch0.get("message")
                if isinstance(m, dict):
                    text = (m.get("content") or "").strip()
                if not text:
                    text = (ch0.get("text") or "").strip()
    return text


def _parse_contact_block(text: str) -> dict:
    """
    Parses a <<BEGIN>>...<<END>> block into a dict.
    Fields: ORG, TITLE, ROLE, CATEGORIES (single-line) + NOTE (multiline).
    """
    begin = _BEGIN_RE.search(text)
    end = _END_RE.search(text)
    if begin and end and end.start() > begin.end():
        text = text[begin.end():end.start()]
    elif begin:
        text = text[begin.end():]

    out = {}
    note_lines = []
    in_note = False

    for line in text.splitlines():
        stripped = line.strip()

        if in_note:
            label_match = False
            for _, regex in _CONTACT_LABELS:
                if regex.match(stripped):
                    label_match = True
                    break
            if label_match or _END_RE.match(stripped):
                in_note = False
            else:
                note_lines.append(line.rstrip())
                continue

        if not stripped:
            continue

        m = _NOTE_RE.match(stripped)
        if m:
            in_note = True
            rest = stripped[m.end():].strip()
            if rest:
                note_lines.append(rest)
            continue

        for key, regex in _CONTACT_LABELS:
            m = regex.match(stripped)
            if m:
                val = stripped[m.end():].strip().strip('"').strip("'")
                if val:
                    out[key] = val
                break

    note_text = "\n".join(note_lines).strip()
    if note_text:
        out["NOTE"] = note_text

    return out


def _format_emails_for_contact_prompt(emails: list[dict]) -> str:
    """
    Formats collected emails with [INCOMING]/[OUTGOING] labels
    for the contact prompt.
    """
    parts = []
    for i, e in enumerate(emails, start=1):
        direction = (e.get("direction") or "incoming").upper()
        parts.append(f"--- Mail {i} [{direction}] ---")
        parts.append(f"From: {(e.get('from') or '').strip()}")
        parts.append(f"To: {(e.get('to') or '').strip()}")
        if e.get("cc"):
            parts.append(f"Cc: {(e.get('cc') or '').strip()}")
        parts.append(f"Subject: {(e.get('subject') or '').strip()}")
        if e.get("date"):
            parts.append(f"Date: {e.get('date')}")
        parts.append((e.get("body") or "")[:2000])
        parts.append("")
    return "\n".join(parts)


_LANG_NAMES = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch", "pt": "Portuguese"}


def build_contact_card(model: str, contact_addr: str, person: str,
                       ollama_url: str, contact_prompt_base: str,
                       collected_emails: list[dict],
                       existing_contact: dict | None = None,
                       llm_profile: dict | None = None,
                       language: str = "en") -> dict | None:
    """
    Builds a contact card from collected IMAP material.

    1. Rule-based: extract_from_headers + extract_from_signature over all incoming mails
    2. LLM call: prompt + formatted material -> NOTE + ORG/TITLE/ROLE/CATEGORIES
    3. merge_contact() -> finished vCard dict

    Returns finished vCard dict, or None on error.
    """
    if not collected_emails:
        return None

    # --- Aggregate rule-based extraction over all incoming mails ---
    best_header = {}
    best_sig = {}
    for e in collected_emails:
        if e.get("direction") != "incoming":
            continue
        h = extract_from_headers(e)
        for k, v in h.items():
            if v and k not in best_header:
                best_header[k] = v
        s = extract_from_signature(e.get("body") or "")
        for k, v in s.items():
            if k == "TEL":
                existing = best_sig.get("TEL") or []
                for t in (v if isinstance(v, list) else [v]):
                    if t not in existing:
                        existing.append(t)
                best_sig["TEL"] = existing
            elif v and k not in best_sig:
                best_sig[k] = v

    # --- LLM call ---
    llm_info = {}
    email_section = _format_emails_for_contact_prompt(collected_emails)
    note_language = _LANG_NAMES.get(language, language)
    prompt = contact_prompt_base.replace("{person}", person).replace("{note_language}", note_language)
    prompt += f"\nThe contact is: {contact_addr}\n\n{email_section}\n"

    opts = profile_to_options(llm_profile, is_thread=False) if llm_profile else {
        "num_ctx": 32768, "num_predict": 4000, "temperature": 0.1, "top_p": 0.85,
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": opts,
    }

    try:
        resp = requests.post(
            ollama_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        if resp.status_code == 200:
            text = _extract_response_text(resp.json())
            if text:
                llm_info = _parse_contact_block(text)
        else:
            log.debug("Contact-LLM HTTP %d", resp.status_code)
    except Exception as e:
        log.debug("Contact-LLM error: %s", e)

    # --- Merge ---
    return merge_contact(existing_contact, best_header, best_sig, llm_info)
