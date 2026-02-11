"""
contacts.py -- Kontakt-Wissensbasis: pro E-Mail-Adresse eine vCard 3.0 Datei.

Abhaengigkeiten innerhalb des Pakets:
  - utils (log)
  - vcard (read_vcard, write_vcard)

Dieses Modul ist ein Blattmodul. Es wird nur von main.py importiert.
Kontakt-Dateien liegen in contacts/ (eine .vcf pro Adresse).

Hauptfunktionen:
  - load_contact / save_contact: vCard laden/speichern
  - format_contact_for_prompt: Kontakt als Prompt-Fragment
  - build_contact_card: IMAP-Material -> regelbasiert + LLM -> vCard-dict
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import copy
import os
import re
import uuid
from datetime import datetime, timezone

import requests

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import log
from email_report.vcard import read_vcard, write_vcard


# ============================================================
# Konstanten
# ============================================================
_CONTACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contacts")

_PRODID = "-//Inbox Sentinel//EN"

# Werte die beim Merge ignoriert werden (LLM-Platzhalter fuer "weiss nicht")
_SKIP_VALUES = frozenset({
    "nicht bestimmbar", "nicht beurteilbar", "unbekannt",
    "keine neuen informationen", "n/a", "-", "\u2014", "",
})

# Regex fuer Telefonnummern in Signaturen
_TEL_LINE_RE = re.compile(
    r"(?:Tel\.?|Fon|Phone|Mobil|Fax|Telefon|Handy)\s*[:.]?\s*"
    r"([\+\d][\d\s/\-().]{6,})",
    re.IGNORECASE,
)
_TEL_INTL_RE = re.compile(r"\+\d[\d\s/\-().]{7,}")

# Regex fuer URLs in Signaturen (keine mailto:, keine Tracking-Pixel)
_SIG_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRACKING_URL_RE = re.compile(r"(?i)(?:track|pixel|click|open|beacon|unsubscribe|list-unsubscribe)")


# ============================================================
# Hilfsfunktionen
# ============================================================
def _email_to_filename(email_addr: str) -> str:
    """Wandelt eine E-Mail-Adresse in einen Dateinamen um."""
    return email_addr.strip().lower().replace("@", "_") + ".vcf"


def _clean_display_name(raw: str) -> str:
    """Extrahiert sauberen Namen aus IMAP-Header-Format wie '"Mustermann, Max" <max@...>'."""
    if not raw:
        return ""
    m = re.match(r'"?([^"<]+)"?\s*<', raw)
    if m:
        return m.group(1).strip().strip('"')
    return raw.strip()


def _ensure_contacts_dir() -> str:
    """Erstellt das Kontaktverzeichnis falls noetig und gibt den Pfad zurueck."""
    os.makedirs(_CONTACTS_DIR, exist_ok=True)
    return _CONTACTS_DIR


def _split_name(display_name: str) -> dict:
    """
    Heuristik: Display-Name in family/given splitten.
    Erkennt 'Nachname, Vorname' und 'Vorname Nachname'.
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
    """Prueft ob ein Wert ein LLM-Platzhalter ist und ignoriert werden soll."""
    return (val or "").strip().lower().rstrip(".") in _SKIP_VALUES


# Gaengige Telefonnummer-Formate (nach Bereinigung):
#   +49 234 777 27 121    (international mit Leerzeichen)
#   +49-234-777-27-121    (international mit Bindestrichen)
#   +49 (0) 234 777 27121 (international mit Null-Klammer)
#   0234 777 27 121       (national)
#   0234/77727121         (national mit Slash)
# Minimum 7 Ziffern, Maximum 15 (ITU-T E.164).
_TEL_VALID_RE = re.compile(
    r"^\+?\d[\d\s/\-()]{5,}$"
)


def _sanitize_tel(raw: str) -> str:
    """
    Bereinigt eine Telefonnummer:
    - Trailing/Leading Muell entfernen (Klammern, Punkte, Kommas, Semikolons)
    - Unbalancierte Klammern reparieren oder entfernen
    - Validierung: 7-15 Ziffern, nur erlaubte Zeichen
    Gibt leeren String zurueck wenn nicht reparierbar.
    """
    s = raw.strip()
    if not s:
        return ""

    # Trailing Muell abschneiden (alles was nicht Ziffer/Klammer-zu ist)
    s = re.sub(r'[.,;:\s]+$', '', s)
    # Trailing offene Klammer (z.B. "...121 (")
    s = re.sub(r'\s*\(\s*$', '', s)
    # Leading Muell (alles vor + oder erster Ziffer)
    s = re.sub(r'^[^+\d]+', '', s)

    # Klammern balancieren: nur "(0)" oder "(0xx)" Muster behalten
    # Alles andere: Klammern entfernen
    balanced = []
    i = 0
    while i < len(s):
        if s[i] == '(':
            close = s.find(')', i)
            if close > i:
                inner = s[i+1:close]
                # Nur behalten wenn Inhalt wie (0) oder (0234) aussieht
                if re.match(r'^0\d{0,4}$', inner.strip()):
                    balanced.append(s[i:close+1])
                    i = close + 1
                    continue
                else:
                    # Klammern entfernen, Inhalt behalten
                    balanced.append(inner)
                    i = close + 1
                    continue
            else:
                # Keine schliessende Klammer: weglassen
                i += 1
                continue
        elif s[i] == ')':
            # Verwaiste schliessende Klammer: weglassen
            i += 1
            continue
        else:
            balanced.append(s[i])
            i += 1
    s = ''.join(balanced).strip()

    # Whitespace normalisieren
    s = re.sub(r'\s+', ' ', s).strip()

    # Ziffern zaehlen (E.164: min 7, max 15)
    digits = re.sub(r'\D', '', s)
    if len(digits) < 7 or len(digits) > 15:
        return ""

    # Nur erlaubte Zeichen?
    if not _TEL_VALID_RE.match(s):
        return ""

    return s


_EMAIL_VALID_RE = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def _sanitize_email(raw: str) -> str:
    """
    Bereinigt und validiert eine E-Mail-Adresse.
    Gibt leeren String zurueck wenn ungueltig.
    """
    s = raw.strip().lower()
    if not s:
        return ""

    # Trailing/Leading Muell
    s = s.strip('<>"\' ')

    if not _EMAIL_VALID_RE.match(s):
        return ""

    return s


# ============================================================
# Laden / Speichern
# ============================================================
def load_contact(email_addr: str) -> dict | None:
    """Laedt einen Kontakt aus vCard. Gibt None zurueck wenn nicht vorhanden."""
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    data = read_vcard(path)
    if data is None:
        log.debug("Kein Kontakt fuer %s", email_addr)
    return data


def save_contact(email_addr: str, data: dict) -> None:
    """Speichert einen Kontakt als vCard (0o600 Permissions). Setzt REV automatisch."""
    _ensure_contacts_dir()
    data["REV"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not data.get("PRODID"):
        data["PRODID"] = _PRODID
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    write_vcard(path, data)


# ============================================================
# Regelbasierte Extraktion aus E-Mail-Headern
# ============================================================
def extract_from_headers(email_dict: dict) -> dict:
    """
    Extrahiert Kontakt-Infos regelbasiert aus E-Mail-Headern.
    Gibt ein partielles vCard-dict zurueck (nur befuellte Felder).
    """
    result = {}

    # FN aus From-Header
    from_raw = (email_dict.get("from") or "").strip()
    fn = _clean_display_name(from_raw)
    if fn:
        result["FN"] = fn
        n = _split_name(fn)
        if n:
            result["N"] = n

    # EMAIL aus from_addr (mit Validierung)
    email_addr = _sanitize_email((email_dict.get("from_addr") or ""))
    if email_addr:
        result["EMAIL"] = email_addr

    # TZ aus Date-Header (Timezone-Offset extrahieren)
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
# Regelbasierte Extraktion aus Signatur
# ============================================================
def extract_from_signature(body: str) -> dict:
    """
    Extrahiert TEL und URL aus dem Signaturbereich (letzte ~15 Zeilen).
    Gibt ein partielles vCard-dict zurueck.
    """
    result = {}
    if not body:
        return result

    lines = body.strip().splitlines()
    sig_lines = lines[-15:] if len(lines) > 15 else lines
    sig_text = "\n".join(sig_lines)

    # Telefonnummern (mit Sanitizing)
    tels = []
    for line in sig_lines:
        m = _TEL_LINE_RE.search(line)
        if m:
            tel = _sanitize_tel(m.group(1))
            if tel and tel not in tels:
                tels.append(tel)
    # Internationale Nummern ohne Label
    for m in _TEL_INTL_RE.finditer(sig_text):
        tel = _sanitize_tel(m.group(0))
        if tel and tel not in tels:
            tels.append(tel)
    if tels:
        result["TEL"] = tels

    # URLs (keine mailto:, keine Tracking)
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
# Prompt-Formatierung
# ============================================================
def format_contact_for_prompt(contact_data: dict | None) -> str:
    """
    Formatiert ein vCard-Kontakt als Prompt-Fragment.
    Gibt leeren String zurueck wenn None oder keine nuetzlichen Daten.
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

    # vCard-Felder direkt mappen
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

    # NOTE: nur LLM-Sektion (vor ---\nUser:) als Kontext
    note = (contact_data.get("NOTE") or "").strip()
    if note:
        user_sep = note.find("---\nUser:")
        llm_note = note[:user_sep].strip() if user_sep >= 0 else note.strip()
        if llm_note:
            lines.append(f"Profile notes:\n{llm_note}")

    lines.append("--- END SENDER CONTEXT ---")

    # Nur zurueckgeben wenn mehr als Rahmen + Identitaet vorhanden
    if len(lines) <= 3:
        return ""

    return "\n".join(lines) + "\n\n"


# ============================================================
# Kontakt-Merge
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
    Merged regelbasierte + LLM-extrahierte Infos in einen bestehenden Kontakt.
    Prioritaet: header_info > sig_info > llm_info (fuer ueberlappende Felder).
    NOTE: LLM-Sektion wird komplett ersetzt, User-Sektion (---\\nUser:) bleibt erhalten.
    """
    if existing:
        contact = copy.deepcopy(existing)
    else:
        contact = copy.deepcopy(_EMPTY_VCARD)

    # UID beibehalten oder neu generieren
    if not contact.get("UID"):
        contact["UID"] = str(uuid.uuid4())

    # Felder mergen: LLM zuerst (niedrigste Prio), dann sig, dann header (hoechste)
    # Einfache String-Felder
    simple_fields = ["FN", "ORG", "TITLE", "ROLE", "NICKNAME",
                     "ADR", "URL", "BDAY", "CATEGORIES", "TZ", "GEO"]
    for source in [llm_info, sig_info, header_info]:
        for key in simple_fields:
            val = (source.get(key) or "").strip()
            if val and not _is_skip_value(val):
                contact[key] = val

    # EMAIL: separat mit Validierung
    for source in [llm_info, sig_info, header_info]:
        raw_email = (source.get("EMAIL") or "").strip()
        if raw_email:
            clean = _sanitize_email(raw_email)
            if clean:
                contact["EMAIL"] = clean

    # N-Feld (strukturiert)
    for source in [llm_info, sig_info, header_info]:
        n = source.get("N")
        if n and isinstance(n, dict) and (n.get("family") or n.get("given")):
            contact["N"] = n

    # TEL: Merge (Listen vereinigen, alle sanitizen)
    existing_tels = contact.get("TEL") or []
    if isinstance(existing_tels, str):
        existing_tels = [existing_tels] if existing_tels else []
    # Bestehende TELs re-sanitizen (alte unsaubere Eintraege bereinigen)
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

    # NOTE: LLM-Sektion komplett ersetzen, User-Sektion bewahren
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

    # SORT-STRING aus N.family ableiten
    n = contact.get("N")
    if isinstance(n, dict) and n.get("family"):
        contact["SORT-STRING"] = n["family"]

    return contact


# ============================================================
# LLM-Extraktion + Contact-Card Builder
# ============================================================
_BEGIN_RE = re.compile(r"<<\s*BEGIN\s*>>", re.IGNORECASE)
_END_RE = re.compile(r"<<\s*END\s*>>", re.IGNORECASE)

# Einzeilige Felder im Contact-Block
_CONTACT_LABELS = [
    ("ORG", re.compile(r"(?i)^ORG\s*[:=\-]\s*")),
    ("TITLE", re.compile(r"(?i)^TITLE\s*[:=\-]\s*")),
    ("ROLE", re.compile(r"(?i)^ROLE\s*[:=\-]\s*")),
    ("CATEGORIES", re.compile(r"(?i)^CATEGORIES\s*[:=\-]\s*")),
]

# NOTE ist ein mehrzeiliges Feld (letztes vor <<END>>)
_NOTE_RE = re.compile(r"(?i)^NOTE\s*[:=\-]\s*")


def _extract_response_text(data) -> str:
    """Extrahiert Response-Text aus Ollama/OpenAI-kompatibler Antwort."""
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
    Parst einen <<BEGIN>>...<<END>> Block in ein dict.
    Felder: ORG, TITLE, ROLE, CATEGORIES (einzeilig) + NOTE (mehrzeilig).
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
    Formatiert gesammelte Mails mit [INCOMING]/[OUTGOING] Labels
    fuer den Contact-Prompt.
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


def build_contact_card(model: str, contact_addr: str, person: str,
                       ollama_url: str, contact_prompt_base: str,
                       collected_emails: list[dict],
                       existing_contact: dict | None = None) -> dict | None:
    """
    Baut eine Kontakt-Card aus gesammeltem IMAP-Material.

    1. Regelbasiert: extract_from_headers + extract_from_signature ueber alle incoming Mails
    2. LLM-Call: Prompt + formatiertes Material -> NOTE + ORG/TITLE/ROLE/CATEGORIES
    3. merge_contact() -> fertiges vCard-dict

    Gibt fertiges vCard-dict zurueck oder None bei Fehler.
    """
    if not collected_emails:
        return None

    # --- Regelbasierte Extraktion ueber alle incoming Mails aggregieren ---
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

    # --- LLM-Call ---
    llm_info = {}
    email_section = _format_emails_for_contact_prompt(collected_emails)
    prompt = contact_prompt_base.replace("{person}", person)
    prompt += f"\nThe contact is: {contact_addr}\n\n{email_section}\n"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 32768,
            "num_predict": 4000,
            "temperature": 0.1,
            "top_p": 0.85,
        },
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
        log.debug("Contact-LLM Fehler: %s", e)

    # --- Merge ---
    return merge_contact(existing_contact, best_header, best_sig, llm_info)
