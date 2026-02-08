"""
drafts.py – Auto-Draft: LLM-generierte Antwortentwuerfe als IMAP Drafts ablegen.

Abhaengigkeiten innerhalb des Pakets:
  - threading (format_thread_for_llm, normalize_subject)
  - utils (log)

Drei Funktionen:
  1) generate_draft_text: LLM-Call fuer Draft-Text
  2) build_draft_message: RFC-2822-konforme Message bauen
  3) imap_save_drafts: Drafts per IMAP APPEND speichern
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import imaplib
import re
from email.mime.text import MIMEText
from email.utils import formatdate

import requests

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import log
from email_report.threading import format_thread_for_llm, normalize_subject


# ============================================================
# 1) Draft-Text per LLM generieren
# ============================================================
def generate_draft_text(model: str, thread: list[dict], person: str, ollama_url: str,
                        draft_prompt_base: str, parsed_analysis: dict, roles: str = "",
                        sender_context: str = "") -> str:
    """
    Generiert einen Antwort-Entwurf per LLM.

    Gibt den Draft-Text zurueck, bei Fehler leeren String.
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
            "num_predict": 2000,
        },
    }

    try:
        resp = requests.post(ollama_url, json=payload,
                             headers={"Content-Type": "application/json"}, timeout=180)
        if resp.status_code != 200:
            log.warning("Draft-LLM HTTP %d: %s", resp.status_code, resp.text[:200])
            return ""

        data = resp.json()

        # Extrahiere Text (gleiche Logik wie llm._extract_llm_text_from_json, aber inline)
        text = ""
        if isinstance(data, dict):
            # Ollama /api/generate
            text = (data.get("response") or "").strip()
            if not text:
                # Chat-Format
                msg = data.get("message")
                if isinstance(msg, dict):
                    text = (msg.get("content") or "").strip()
            if not text:
                # OpenAI-kompatibel
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
        log.warning("Draft-LLM Fehler: %s", e)
        return ""


# ============================================================
# 2) RFC-2822 Draft-Message bauen
# ============================================================
_REPLY_PREFIX_RE = re.compile(r"^(Re|AW|Antwort|Antw|SV|VS|Ref)\s*:\s*", re.IGNORECASE)


def build_draft_message(thread: list[dict], draft_body: str, from_email: str,
                        person_name: str):
    """
    Baut eine RFC-2822-konforme MIME Message fuer den Drafts-Ordner.
    """
    newest = thread[-1]
    subject = (newest.get("subject") or "").strip()

    # Re: Prefix nur hinzufuegen wenn nicht schon vorhanden
    if not _REPLY_PREFIX_RE.match(subject):
        subject = f"Re: {subject}"

    msg = MIMEText(draft_body, "plain", "utf-8")
    msg["From"] = f"{person_name} <{from_email}>"
    msg["To"] = (newest.get("from") or "").strip()
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    # In-Reply-To: Message-ID der neuesten Mail
    newest_mid = (newest.get("message_id") or "").strip()
    if newest_mid:
        msg["In-Reply-To"] = newest_mid

    # References: Kette aller Message-IDs im Thread
    refs = []
    for e in thread:
        mid = (e.get("message_id") or "").strip()
        if mid and mid not in refs:
            refs.append(mid)
    if refs:
        msg["References"] = " ".join(refs)

    return msg


# ============================================================
# 3) Drafts per IMAP APPEND speichern
# ============================================================
def imap_save_drafts(username: str, password: str, imap_server: str, imap_port: int,
                     drafts_folder: str, draft_messages: list) -> dict:
    """
    Speichert Draft-Messages per IMAP APPEND im Drafts-Ordner.

    draft_messages: Liste von (subject_log, Message) Tupeln.

    Gibt {"saved": int, "failed": int, "errors": [...]} zurueck.
    """
    result = {"saved": 0, "failed": 0, "errors": []}

    if not draft_messages:
        return result

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(username, password)

        # Drafts-Ordner erstellen falls noetig
        try:
            status_c, _ = mail.create(drafts_folder)
            if status_c == "OK":
                log.info("IMAP-Ordner erstellt: %s", drafts_folder)
        except Exception:
            pass  # Existiert vermutlich schon
        # Subscribe: ohne Abo zeigen viele Clients den Ordner nicht an
        try:
            mail.subscribe(drafts_folder)
        except Exception:
            pass

        for subject_log, msg in draft_messages:
            try:
                status, _ = mail.append(
                    drafts_folder,
                    "(\\Draft)",
                    None,
                    msg.as_bytes(),
                )
                if status == "OK":
                    result["saved"] += 1
                    log.info("Draft gespeichert: %s", subject_log)
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
