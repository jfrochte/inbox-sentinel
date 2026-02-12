"""
drafts.py -- Auto-draft: LLM-generated reply drafts stored as IMAP drafts.

Dependencies within the package:
  - threading (format_thread_for_llm, normalize_subject)
  - utils (log)

Three functions:
  1) generate_draft_text: LLM call for draft text
  2) build_draft_message: build RFC-2822 compliant message
  3) imap_save_drafts: save drafts via IMAP APPEND
"""

# ============================================================
# External dependencies
# ============================================================
import imaplib
import re
import email.charset as _charset
from email.mime.text import MIMEText
from email.utils import formatdate

# Thunderbird requires quoted-printable for editable drafts (not base64)
_QP_UTF8 = _charset.Charset('utf-8')
_QP_UTF8.body_encoding = _charset.QP

import requests

# ============================================================
# Internal package imports
# ============================================================
from email_report.utils import log
from email_report.threading import format_thread_for_llm, normalize_subject


# ============================================================
# 1) Generate draft text via LLM
# ============================================================
def generate_draft_text(model: str, thread: list[dict], person: str, ollama_url: str,
                        draft_prompt_base: str, parsed_analysis: dict, roles: str = "",
                        sender_context: str = "") -> str:
    """
    Generates a reply draft via LLM.

    Returns the draft text, or empty string on error.
    """
    email_text = format_thread_for_llm(thread)
    newest = thread[-1]

    subject = (parsed_analysis.get("subject") or newest.get("subject") or "").strip()
    sender = (parsed_analysis.get("sender") or newest.get("from") or "").strip()
    summary = (parsed_analysis.get("summary") or "").strip()
    actions = (parsed_analysis.get("actions") or "").strip()

    roles_line = f"\nRoles and responsibilities: {roles}\n" if roles else ""

    prompt = sender_context + draft_prompt_base.format(
        person=person,
        roles=roles_line,
        subject=subject,
        sender=sender,
        summary=summary,
        actions=actions,
        email_text=email_text,
    )

    num_ctx = 65536 if len(thread) >= 2 else 32768

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": 4000,
        },
    }

    try:
        resp = requests.post(ollama_url, json=payload,
                             headers={"Content-Type": "application/json"}, timeout=180)
        if resp.status_code != 200:
            log.warning("Draft-LLM HTTP %d: %s", resp.status_code, resp.text[:200])
            return ""

        data = resp.json()

        # Extract text (same logic as llm._extract_llm_text_from_json, but inline)
        text = ""
        if isinstance(data, dict):
            # Ollama /api/generate format
            text = (data.get("response") or "").strip()
            if not text:
                # Chat format
                msg = data.get("message")
                if isinstance(msg, dict):
                    text = (msg.get("content") or "").strip()
            if not text:
                # OpenAI-compatible
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

    except Exception as e:
        log.warning("Draft-LLM error: %s", e)
        return ""


# ============================================================
# 2) Build RFC-2822 draft message
# ============================================================
_REPLY_PREFIX_RE = re.compile(r"^(Re|AW|Antwort|Antw|SV|VS|Ref)\s*:\s*", re.IGNORECASE)


def _build_full_quote(newest: dict) -> str:
    """Builds the full-quote block from the newest mail in the thread."""
    original = (newest.get("body_original") or newest.get("body") or "").strip()
    if not original:
        return ""

    sender = (newest.get("from") or "").strip()
    date = (newest.get("date") or "").strip()

    quoted = "\n".join("> " + line for line in original.splitlines())
    return f"Am {date} schrieb {sender}:\n{quoted}"


def _load_signature(signature_file: str) -> str:
    """Loads a signature file. Returns empty string if not found."""
    if not signature_file:
        return ""
    try:
        with open(signature_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def build_draft_message(thread: list[dict], draft_body: str, from_email: str,
                        person_name: str, signature_file: str = ""):
    """
    Builds an RFC-2822 compliant MIME message for the drafts folder.
    """
    newest = thread[-1]
    subject = (newest.get("subject") or "").strip()

    # Only add Re: prefix if not already present
    if not _REPLY_PREFIX_RE.match(subject):
        subject = f"Re: {subject}"

    # [Sentinel-Entwurf] prefix: marks LLM-generated drafts
    subject = f"[Sentinel-Entwurf] {subject}"

    # Insert signature (RFC-compliant separator: "-- \n")
    signature = _load_signature(signature_file)
    if signature:
        draft_body = draft_body + "\n\n-- \n" + signature

    # Full-quote: original mail below the LLM draft (two blank lines gap)
    full_quote = _build_full_quote(newest)
    full_body = draft_body + "\n\n\n" + full_quote if full_quote else draft_body

    msg = MIMEText(full_body, "plain", _charset=_QP_UTF8)
    msg["From"] = f"{person_name} <{from_email}>"
    msg["To"] = (newest.get("from") or "").strip()
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    # Client-specific draft headers for editability
    msg["X-Mozilla-Draft-Info"] = "internal/draft; vcard=0; receipt=0; DSN=0; uuencode=0; attachmentreminder=0; deliveryformat=4"
    msg["X-Uniform-Type-Identifier"] = "com.apple.mail-draft"

    # In-Reply-To: Message-ID of the newest mail
    newest_mid = (newest.get("message_id") or "").strip()
    if newest_mid:
        msg["In-Reply-To"] = newest_mid

    # References: chain of all Message-IDs in the thread
    refs = []
    for e in thread:
        mid = (e.get("message_id") or "").strip()
        if mid and mid not in refs:
            refs.append(mid)
    if refs:
        msg["References"] = " ".join(refs)

    return msg


# ============================================================
# 3) Save drafts via IMAP APPEND
# ============================================================
def _detect_drafts_folder(mail) -> str | None:
    """Detects the \\Drafts special-use folder via IMAP LIST (RFC 6154).

    Returns the folder name, or None if not detected.
    """
    try:
        status, folder_list = mail.list()
        if status != "OK" or not folder_list:
            return None
        for item in folder_list:
            if item is None:
                continue
            text = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
            if "\\Drafts" in text:
                # Format: '(\HasNoChildren \Drafts) "/" "Entwuerfe"'
                # oder:   '(\HasNoChildren \Drafts) "." Drafts'
                m = re.search(r'\) "." (?:"([^"]+)"|(\S+))$', text)
                if m:
                    return m.group(1) or m.group(2)
        return None
    except Exception:
        return None


def imap_save_drafts(username: str, password: str, imap_server: str, imap_port: int,
                     drafts_folder: str, draft_messages: list) -> dict:
    """
    Saves draft messages via IMAP APPEND to the drafts folder.

    Auto-detects the \\Drafts special-use folder (RFC 6154).
    Fallback: drafts_folder parameter.

    draft_messages: list of (subject_log, Message) tuples.

    Returns {"saved": int, "failed": int, "errors": [...]}.
    """
    result = {"saved": 0, "failed": 0, "errors": []}

    if not draft_messages:
        return result

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(username, password)

        # Auto-detect: \Drafts special-use folder (RFC 6154)
        detected = _detect_drafts_folder(mail)
        if detected:
            actual_folder = detected
            log.info("Drafts: \\Drafts Ordner erkannt: %s", actual_folder)
        else:
            actual_folder = drafts_folder
            log.info("Drafts: Kein \\Drafts Ordner erkannt, nutze Fallback: %s",
                     actual_folder)
            # Create fallback folder if needed
            try:
                status_c, _ = mail.create(actual_folder)
                if status_c == "OK":
                    log.info("IMAP-Ordner erstellt: %s", actual_folder)
            except Exception:
                pass  # Probably already exists
            try:
                mail.subscribe(actual_folder)
            except Exception:
                pass

        for subject_log, msg in draft_messages:
            try:
                status, _ = mail.append(
                    actual_folder,
                    "(\\Draft \\Seen)",
                    None,
                    msg.as_bytes(),
                )
                if status == "OK":
                    result["saved"] += 1
                    log.info("Draft gespeichert in '%s': %s", actual_folder, subject_log)
                else:
                    result["failed"] += 1
                    result["errors"].append(f"APPEND fehlgeschlagen fuer: {subject_log}")
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"Draft '{subject_log}': {e}")
                log.warning("Draft-Fehler fuer '%s': %s", subject_log, e)

    except Exception as e:
        result["failed"] += len(draft_messages) - result["saved"]
        result["errors"].append(f"IMAP-Verbindung: {e}")
        log.warning("Draft IMAP-Fehler: %s", e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return result
