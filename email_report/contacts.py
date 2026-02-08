"""
contacts.py – Kontakt-Wissensbasis: pro E-Mail-Adresse ein JSON-Characterblatt.

Abhaengigkeiten innerhalb des Pakets:
  - utils (log, write_secure)

Dieses Modul ist ein Blattmodul. Es wird nur von main.py importiert.
Kontakt-Dateien liegen in contacts/ (ein JSON pro Adresse).
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import json
import os
import re

import requests

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import log, write_secure


# ============================================================
# Konstanten
# ============================================================
_CONTACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contacts")

_EMPTY_CONTACT = {
    "email": "",
    "name": "",
    "tone": {"value": "", "updated": ""},
    "language": {"value": "", "updated": ""},
    "role_or_title": {"value": "", "updated": ""},
    "relationship": {"value": "", "updated": ""},
    "communication_style": {"value": "", "updated": ""},
    "topics": [],
    "last_contact": "",
    "contact_count": 0,
    "notes": "",
}


# ============================================================
# Hilfsfunktionen
# ============================================================
def _email_to_filename(email_addr: str) -> str:
    """Wandelt eine E-Mail-Adresse in einen Dateinamen um."""
    return email_addr.strip().lower().replace("@", "_") + ".json"


def _ensure_contacts_dir() -> str:
    """Erstellt das Kontaktverzeichnis falls noetig und gibt den Pfad zurueck."""
    os.makedirs(_CONTACTS_DIR, exist_ok=True)
    return _CONTACTS_DIR


# ============================================================
# Laden / Speichern
# ============================================================
def load_contact(email_addr: str) -> dict | None:
    """Laedt einen Kontakt aus JSON. Gibt None zurueck wenn nicht vorhanden."""
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.debug("Kontakt-Datei nicht lesbar %s: %s", path, e)
        return None


def save_contact(email_addr: str, data: dict) -> None:
    """Speichert einen Kontakt als JSON (0o600 Permissions)."""
    _ensure_contacts_dir()
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    write_secure(path, json.dumps(data, ensure_ascii=False, indent=2))


# ============================================================
# Prompt-Formatierung
# ============================================================
def format_contact_for_prompt(contact_data: dict | None) -> str:
    """
    Formatiert ein Kontakt-Characterblatt als Prompt-Fragment.
    Gibt leeren String zurueck wenn None oder keine nuetzlichen Daten.
    """
    if not contact_data:
        return ""

    name = (contact_data.get("name") or "").strip()
    email = (contact_data.get("email") or "").strip()
    if not name and not email:
        return ""

    lines = ["--- SENDER CONTEXT ---"]

    ident = f"Known sender: {name}" if name else "Known sender"
    if email:
        ident += f" ({email})"
    lines.append(ident)

    # Felder mit updated-Datum
    field_map = [
        ("role_or_title", "Role/Title"),
        ("relationship", "Relationship"),
        ("tone", "Communication tone"),
        ("communication_style", "Communication style"),
        ("language", "Preferred language"),
    ]
    for key, label in field_map:
        field = contact_data.get(key)
        if isinstance(field, dict):
            val = (field.get("value") or "").strip()
            updated = (field.get("updated") or "").strip()
            if val:
                entry = f"{label}: {val}"
                if updated:
                    entry += f" (as of {updated})"
                lines.append(entry)

    # Topics
    topics = contact_data.get("topics") or []
    if topics:
        parts = []
        for t in topics[-5:]:
            if isinstance(t, dict):
                topic = (t.get("topic") or "").strip()
                date = (t.get("date") or "").strip()
                if topic:
                    parts.append(f"{topic} ({date})" if date else topic)
            elif isinstance(t, str) and t.strip():
                parts.append(t.strip())
        if parts:
            lines.append(f"Recent topics: {', '.join(parts)}")

    count = contact_data.get("contact_count", 0)
    if count:
        lines.append(f"Previous emails processed: {count}")

    notes = (contact_data.get("notes") or "").strip()
    if notes:
        lines.append(f"User notes: {notes}")

    lines.append("--- END SENDER CONTEXT ---")

    # Nur zurueckgeben wenn mehr als Rahmen + Identitaet vorhanden
    if len(lines) <= 3:
        return ""

    return "\n".join(lines) + "\n\n"


# ============================================================
# Kontakt-Merge
# ============================================================
def merge_contact_update(existing: dict | None, llm_extracted: dict,
                         email_addr: str, display_name: str, email_date: str) -> dict:
    """
    Merged LLM-extrahierte Infos in einen bestehenden (oder neuen) Kontakt.
    KRITISCH: notes wird NIE automatisch ueberschrieben.
    """
    import copy
    if existing:
        contact = copy.deepcopy(existing)
    else:
        contact = copy.deepcopy(_EMPTY_CONTACT)

    # Basis-Felder
    contact["email"] = email_addr
    if display_name and (not contact.get("name") or not contact["name"].strip()):
        contact["name"] = display_name

    # Felder mit value/updated
    dated_fields = ["tone", "language", "role_or_title", "relationship", "communication_style"]
    for key in dated_fields:
        new_val = (llm_extracted.get(key) or "").strip()
        if new_val:
            if not isinstance(contact.get(key), dict):
                contact[key] = {"value": "", "updated": ""}
            contact[key]["value"] = new_val
            contact[key]["updated"] = email_date

    # Topics: neue anhaengen, letzte 10 behalten
    new_topics = llm_extracted.get("topics") or []
    existing_topics = contact.get("topics") or []
    if not isinstance(existing_topics, list):
        existing_topics = []
    for t in new_topics:
        if isinstance(t, str) and t.strip():
            existing_topics.append({"topic": t.strip(), "date": email_date})
        elif isinstance(t, dict) and (t.get("topic") or "").strip():
            if not t.get("date"):
                t["date"] = email_date
            existing_topics.append(t)
    contact["topics"] = existing_topics[-10:]

    # Zaehler und Datum
    contact["contact_count"] = (contact.get("contact_count") or 0) + 1
    if email_date:
        contact["last_contact"] = email_date

    # KRITISCH: notes NIE ueberschreiben
    # (bleibt wie im existing oder leer bei neuem Kontakt)

    return contact


# ============================================================
# LLM-Extraktion
# ============================================================
_JSON_RE = re.compile(r"\{[^}]*\}", re.DOTALL)


def extract_contact_info_via_llm(model: str, thread: list[dict], person: str,
                                  ollama_url: str) -> dict:
    """
    Extrahiert Kontakt-Infos aus der neuesten Mail per LLM.
    Gibt ein dict mit Feldern zurueck, bei Fehler leeres dict.
    """
    newest = thread[-1]
    body = (newest.get("body") or "")[:3000]
    sender = (newest.get("from") or "").strip()
    subject = (newest.get("subject") or "").strip()

    prompt = f"""Analyze this email and extract information about the sender.
You are working for {person}. The sender is: {sender}

Email subject: {subject}
Email body:
{body}

Return ONLY a JSON object with these fields (leave empty string if unknown):
- "tone": How does the sender communicate? e.g. "formal, siezt", "informell, duzt", "neutral"
- "language": Language of the email, e.g. "de", "en", "de+en gemischt"
- "role_or_title": Sender's role or title if recognizable, e.g. "Professor fuer Informatik"
- "relationship": Relationship to {person}, e.g. "Kollege", "externer Dienstleister"
- "communication_style": Detailed description of how the sender communicates: formal/informal, greeting style, directness, typical patterns
- "topics": List of 1-3 keywords about the current email content

Return ONLY the JSON, no explanation."""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 8192,
            "num_predict": 800,
        },
    }

    try:
        resp = requests.post(ollama_url, json=payload,
                             headers={"Content-Type": "application/json"}, timeout=120)
        if resp.status_code != 200:
            log.debug("Contact-LLM HTTP %d", resp.status_code)
            return {}

        data = resp.json()

        # Text extrahieren (gleiche Logik wie drafts.py, inline)
        text = ""
        if isinstance(data, dict):
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

        if not text:
            return {}

        # JSON extrahieren
        m = _JSON_RE.search(text)
        if not m:
            return {}

        result = json.loads(m.group(0))
        if not isinstance(result, dict):
            return {}

        return result

    except Exception as e:
        log.debug("Contact-LLM Fehler: %s", e)
        return {}
