"""
email_parser.py – MIME parsing, body extraction and reply/forward splitting.

This module is a leaf module with no internal package dependencies.
It handles everything related to parsing individual email messages:
header decoding, HTML-to-text conversion, separation of newest content
from quoted history, and selection of the best body text from
multipart messages.
"""

# ============================================================
# External dependencies
# ============================================================
import re
import email
import email.message
from email.header import decode_header
from email.utils import parseaddr

from bs4 import BeautifulSoup
from bs4 import FeatureNotFound


# ============================================================
# Helper functions: email parsing
# ============================================================
def decode_mime_words(s):
    """
    Decodes MIME-encoded headers such as Subject/From.
    Robust against unknown encodings by falling back to utf-8.
    """
    if not s:
        return ""
    out = []
    for chunk, charset in decode_header(s):
        if isinstance(chunk, bytes):
            cs = charset or "utf-8"
            try:
                out.append(chunk.decode(cs, errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def get_email_address_from_header(header_value: str) -> str:
    """
    Extracts the raw email address from a header field.
    """
    _name, addr = parseaddr(header_value or "")
    return (addr or "").strip()


def html_to_text(html_str: str) -> str:
    """
    HTML -> Text (BeautifulSoup).
    Normalises line breaks and reduces excessive blank lines.
    """
    try:
        soup = BeautifulSoup(html_str, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html_str, "html.parser")

    text = soup.get_text(separator="\n")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ============================================================
# Newest content vs. quoted history
# ============================================================
RE_REPLY_MARKERS = [
    # typical Outlook/Thunderbird separators
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Ursprüngliche Nachricht\s*-{2,}\s*$", re.I),

    # "On ... wrote:" (EN)
    re.compile(r"^\s*On .+ wrote:\s*$", re.I),

    # "Am ... schrieb ...:" (DE)
    re.compile(r"^\s*Am .+ schrieb .+:\s*$", re.I),

    # Header block in replies/forwards (DE/EN)
    # We intentionally avoid matching every header line; instead we detect
    # a block start that typically begins with "From:" or "Von:".
    re.compile(r"^\s*(From|Von)\s*:\s*.+$", re.I),
]


def split_newest_and_history(text: str):
    """
    Separates the newest part of an email from the quoted history.

    Heuristic:
    - Find the first line that looks like a reply/forward marker.
    - Everything before it is treated as the new part.
    - Everything from the marker onward is treated as history.

    Advantage:
    - The LLM sees the current message more clearly at the top.
    Disadvantage:
    - The heuristic may occasionally cut too early depending on content.
    """
    if not text:
        return "", ""

    lines = text.splitlines()
    cut_idx = None

    for i, line in enumerate(lines):
        # Only cut when:
        # - a marker matches AND
        # - there was some content before it (prevents false positives at the top)
        if i >= 2:
            for rx in RE_REPLY_MARKERS:
                if rx.match(line.strip()):
                    cut_idx = i
                    break
        if cut_idx is not None:
            break

    if cut_idx is None:
        return text.strip(), ""

    newest = "\n".join(lines[:cut_idx]).strip()
    history = "\n".join(lines[cut_idx:]).strip()
    return newest, history


def quote_history_block(history: str) -> str:
    """
    Quotes the history with a simple "> " prefix.
    Important: no incremental quote-level counting (the old heuristic was too aggressive).
    """
    if not history:
        return ""
    return "\n".join(["> " + ln for ln in history.splitlines()]).strip()


# ============================================================
# Body selection (not just first part)
# ============================================================
def _score_candidate(text: str) -> int:
    """
    Scores a text candidate for usable body content.
    Very simple scoring idea:
    - many non-whitespace characters => good
    - very high proportion of quoted lines (>) => likely history => worse
    - extremely short texts => bad
    """
    if not text:
        return 0

    stripped = text.strip()
    if len(stripped) < 20:
        return 0

    lines = stripped.splitlines()
    non_empty_lines = [ln for ln in lines if ln.strip()]
    if not non_empty_lines:
        return 0

    quoted_lines = sum(1 for ln in non_empty_lines if ln.lstrip().startswith(">"))
    quote_ratio = quoted_lines / max(1, len(non_empty_lines))

    # Base: number of visible (non-whitespace) characters
    base = sum(1 for ch in stripped if not ch.isspace())

    # Quote ratio penalises the score because we prefer new content
    penalty = int(base * 0.6 * quote_ratio)

    return max(0, base - penalty)


def extract_best_body_text(message: email.message.Message) -> str:
    """
    Extracts one body text per email, but more robustly:

    1) Collect candidates from all text/plain and text/html parts.
       - Attachments are ignored (Content-Disposition == attachment).
       - Parts with a filename are also ignored (often inline attachments).
    2) HTML candidates are converted to plain text.
    3) Candidates are scored and the best one is selected.
       This is better than always taking the first part because:
       - multipart/alternative often contains multiple variants
       - some plain parts are just stubs ("open in browser")
    4) Afterwards we split into newest/history and quote the history cleanly.
    """
    candidates = []  # List[tuple(score, text, ctype)]

    def add_candidate(raw_text: str, ctype: str):
        if not raw_text:
            return
        newest, history = split_newest_and_history(raw_text)
        # Build the final body so that the new part appears at the top.
        # History is quoted but not escalated.
        if history:
            merged = newest + "\n\nQuoted history (trimmed):\n" + quote_history_block(history)
        else:
            merged = newest

        score = _score_candidate(merged)
        candidates.append((score, merged, ctype))

    # Multipart: iterate over all parts
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue

            ctype = part.get_content_type()
            disp = part.get_content_disposition()  # None, 'inline', 'attachment'
            filename = part.get_filename()

            # Skip attachments
            if disp == "attachment":
                continue

            # Many systems don't set Content-Disposition properly but include a filename.
            # If filename is set, it is usually not actual body content.
            if filename:
                continue

            if ctype not in ("text/plain", "text/html"):
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")

            if ctype == "text/html":
                decoded = html_to_text(decoded)

            add_candidate(decoded, ctype)

    else:
        # Single part
        ctype = message.get_content_type()
        if ctype in ("text/plain", "text/html"):
            payload = message.get_payload(decode=True)
            if payload is not None:
                charset = message.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except LookupError:
                    decoded = payload.decode("utf-8", errors="replace")

                if ctype == "text/html":
                    decoded = html_to_text(decoded)

                add_candidate(decoded, ctype)

    if not candidates:
        return ""

    # Highest score wins.
    # On a tie the order does not matter; we simply take max.
    best = max(candidates, key=lambda x: x[0])
    return best[1].strip()


def extract_raw_body_text(message: email.message.Message) -> str:
    """
    Extracts the complete body text of an email without split/score/quote.

    Uses the same MIME walk logic as extract_best_body_text (skipping
    attachments), but returns the text as-is. When there are multiple
    candidates: text/plain is preferred, otherwise the longest wins.
    """
    candidates = []  # List[tuple(ctype, text)]

    def _decode_part(part):
        payload = part.get_payload(decode=True)
        if payload is None:
            return None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment" or part.get_filename():
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            decoded = _decode_part(part)
            if decoded:
                if ctype == "text/html":
                    decoded = html_to_text(decoded)
                candidates.append((ctype, decoded))
    else:
        ctype = message.get_content_type()
        if ctype in ("text/plain", "text/html"):
            decoded = _decode_part(message)
            if decoded:
                if ctype == "text/html":
                    decoded = html_to_text(decoded)
                candidates.append((ctype, decoded))

    if not candidates:
        return ""

    # Prefer text/plain, otherwise take the longest
    plain = [t for ct, t in candidates if ct == "text/plain"]
    if plain:
        return max(plain, key=len).strip()
    return max((t for _, t in candidates), key=len).strip()
