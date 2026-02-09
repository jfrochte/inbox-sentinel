"""
contacts.py -- Kontakt-Wissensbasis: pro E-Mail-Adresse ein JSON-Characterblatt.

Abhaengigkeiten innerhalb des Pakets:
  - utils (log, write_secure)

Dieses Modul ist ein Blattmodul. Es wird nur von main.py importiert.
Kontakt-Dateien liegen in contacts/ (ein JSON pro Adresse).
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import copy
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
    "profile": {"value": "", "updated": ""},
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


def _clean_display_name(raw: str) -> str:
    """Extrahiert sauberen Namen aus IMAP-Header-Format wie '"Roters, Kai" <kai.roters@...>'."""
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

    # Profile
    profile_field = contact_data.get("profile")
    if isinstance(profile_field, dict):
        profile_val = (profile_field.get("value") or "").strip()
        if profile_val:
            lines.append(f"Profile: {profile_val}")

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
# Profil: Struktur-Sanitizing + Beleg-Strip
# ============================================================
_PROFILE_ALLOWED_HEADERS_RE = re.compile(r"(?im)^\s*(Belegt|Beobachtet)\s*:\s*$")
_PROFILE_ALLOWED_BULLET_RE = re.compile(r"^\s*-\s+")
_BELEG_BULLET_RE = re.compile(
    r'^\s*-\s*(.*?)\s*\(Beleg:\s*["“](.+?)["”]\s*\)\s*$',
    flags=re.IGNORECASE,
)


def _sanitize_profile_structured(text: str) -> str:
    """
    Laesst nur diese Grundstruktur durch:
      Belegt:
      - ...
      Beobachtet:
      - ...
    Alles andere (Paragraphen, Signatur-Dumps, etc.) wird verworfen.
    """
    txt = (text or "").strip()
    if not txt:
        return ""

    out: list[str] = []
    in_section = False
    saw_any_bullet = False

    for raw in txt.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if _PROFILE_ALLOWED_HEADERS_RE.match(stripped):
            # Header normalisieren
            if stripped.lower().startswith("belegt"):
                out.append("Belegt:")
            else:
                out.append("Beobachtet:")
            in_section = True
            continue

        if not in_section:
            continue

        if not stripped:
            continue

        if _PROFILE_ALLOWED_BULLET_RE.match(stripped):
            out.append(stripped)
            saw_any_bullet = True
            continue

        # alles andere verwerfen
        continue

    cleaned = "\n".join(out).strip()
    if not cleaned or not saw_any_bullet:
        return ""

    return cleaned


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def strip_belege_from_profile(profile_text: str, evidence_text: str | None = None) -> str:
    """
    Entfernt '(Beleg: "...")' aus Beobachtet-Bullets.
    Optional: Wenn evidence_text gesetzt ist, werden Bullets verworfen,
    deren Beleg-Zitat nicht im evidence_text vorkommt (Anti-Halluzination).
    """
    if not profile_text:
        return ""

    ev = _norm_ws(evidence_text).lower() if evidence_text else None

    out_lines: list[str] = []
    for line in profile_text.splitlines():
        s = line.rstrip()

        m = _BELEG_BULLET_RE.match(s)
        if m:
            bullet_text = m.group(1).strip()
            quote = _norm_ws(m.group(2)).lower()

            # Optional harte Validierung gegen Halluzination:
            if ev is not None and quote and quote not in ev:
                continue

            out_lines.append(f"- {bullet_text}")
            continue

        out_lines.append(s)

    return "\n".join([ln for ln in out_lines if ln.strip() != ""]).strip()


# ============================================================
# Profil: Bullet-Filter + Akkumulation
# ============================================================
_PLZ_RE = re.compile(r"\b\d{5}\b")
_ROOM_RE = re.compile(r"(?i)\braum\b")
_URL_RE = re.compile(r"https?://")

_DE_STOPWORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "und", "oder", "fuer", "für", "von", "mit", "zu", "zum", "zur",
    "in", "im", "am", "an", "auf", "aus", "bei", "nach", "ueber", "über",
})


def _filter_belegt_bullet(text: str) -> bool:
    """True wenn Belegt-Bullet behalten werden soll (kein Adress-/Raum-/URL-Dump)."""
    if _PLZ_RE.search(text):
        return False
    if _ROOM_RE.search(text):
        return False
    if _URL_RE.search(text):
        return False
    return True


def _filter_beobachtet_bullet(text: str) -> bool:
    """True wenn Beobachtet-Bullet Mindestqualitaet hat (>=3 Woerter, keine URL)."""
    words = text.split()
    if len(words) < 3:
        return False
    if _URL_RE.search(text):
        return False
    return True


def _extract_section_bullets(profile_text: str, section: str) -> list[str]:
    """Extrahiert Bullets aus einer bestimmten Sektion (belegt/beobachtet)."""
    in_section = False
    bullets: list[str] = []
    target = section.lower()
    for line in (profile_text or "").splitlines():
        s = line.strip()
        if _PROFILE_ALLOWED_HEADERS_RE.match(s):
            in_section = s.lower().startswith(target)
            continue
        if in_section and s.startswith("- "):
            bullets.append(s[2:].strip())
    return bullets


def _content_words(text: str) -> set[str]:
    """Gibt Content-Woerter zurueck (ohne Stoppwoerter und Satzzeichen)."""
    raw = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return {w for w in raw if w not in _DE_STOPWORDS and len(w) > 1}


def _bullets_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Prueft ob zwei Bullets thematisch aehnlich genug fuer Dedup sind."""
    wa = _content_words(a)
    wb = _content_words(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    return overlap / min(len(wa), len(wb)) >= threshold


def _rebuild_profile(belegt: list[str], beobachtet: list[str]) -> str:
    """Baut Profil-Text aus Bullet-Listen zusammen."""
    parts: list[str] = []
    if belegt:
        parts.append("Belegt:")
        parts.extend(f"- {b}" for b in belegt)
    if beobachtet:
        parts.append("Beobachtet:")
        parts.extend(f"- {b}" for b in beobachtet)
    return "\n".join(parts)


def _postprocess_profile(new_profile: str, old_profile: str | None,
                         evidence_text: str | None = None) -> str:
    """
    Vollstaendige Post-Processing-Pipeline fuer Profil-Text:
    1. Struktur-Sanitizing (nur Belegt/Beobachtet)
    2. Beleg-Zitate strippen (+ Validierung gegen evidence_text)
    3. Belegt-Bullets filtern (Adressen, Raeume, URLs)
    4. Beobachtet-Bullets filtern (Mindestqualitaet)
    5. Alte Beobachtet-Bullets akkumulieren
    6. Profil neu zusammenbauen
    """
    # 1. Struktur-Sanitizing
    sanitized = _sanitize_profile_structured(new_profile)
    if not sanitized:
        return old_profile or ""

    # 2. Belege strippen
    stripped = strip_belege_from_profile(sanitized, evidence_text=evidence_text)
    if not stripped:
        return old_profile or ""

    # 3. Belegt filtern
    belegt = _extract_section_bullets(stripped, "belegt")
    belegt = [b for b in belegt if _filter_belegt_bullet(b)]

    # 4. Beobachtet filtern
    beobachtet = _extract_section_bullets(stripped, "beobachtet")
    beobachtet = [b for b in beobachtet if _filter_beobachtet_bullet(b)]

    # 5. Alte Beobachtet-Bullets akkumulieren
    if old_profile:
        old_beobachtet = _extract_section_bullets(old_profile, "beobachtet")
        old_beobachtet = [b for b in old_beobachtet if _filter_beobachtet_bullet(b)]
        for ob in old_beobachtet:
            if not any(_bullets_similar(ob, nb) for nb in beobachtet):
                beobachtet.append(ob)
    beobachtet = beobachtet[:6]

    # 6. Zusammenbauen
    rebuilt = _rebuild_profile(belegt, beobachtet)
    return rebuilt or (old_profile or "")


# ============================================================
# Kontakt-Merge
# ============================================================
def merge_contact_update(existing: dict | None, llm_extracted: dict,
                         email_addr: str, display_name: str, email_date: str) -> dict:
    """
    Merged LLM-extrahierte Infos in einen bestehenden (oder neuen) Kontakt.
    KRITISCH: notes wird NIE automatisch ueberschrieben.
    """
    if existing:
        contact = copy.deepcopy(existing)
    else:
        contact = copy.deepcopy(_EMPTY_CONTACT)

    # Basis-Felder
    contact["email"] = email_addr
    if display_name:
        clean_name = _clean_display_name(display_name)
        if clean_name:
            contact["name"] = clean_name

    # Felder mit value/updated
    dated_fields = ["tone", "language", "role_or_title", "relationship",
                    "communication_style", "profile"]
    for key in dated_fields:
        new_val = (llm_extracted.get(key) or "").strip()
        if new_val and new_val.lower().rstrip(".") not in _SKIP_VALUES:
            if not isinstance(contact.get(key), dict):
                contact[key] = {"value": "", "updated": ""}
            contact[key]["value"] = new_val
            contact[key]["updated"] = email_date

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
_BEGIN_RE = re.compile(r"<<\s*BEGIN\s*>>", re.IGNORECASE)
_END_RE = re.compile(r"<<\s*END\s*>>", re.IGNORECASE)

# Label-Mapping: Regex -> dict-Key (einzeilige Felder)
_CONTACT_LABELS = [
    ("tone", re.compile(r"(?i)^tone\s*[:=\-]\s*")),
    ("language", re.compile(r"(?i)^language\s*[:=\-]\s*")),
    ("role_or_title", re.compile(r"(?i)^role(?:[/\s\-]*title)?\s*[:=\-]\s*")),
    ("relationship", re.compile(r"(?i)^relationship\s*[:=\-]\s*")),
    ("communication_style", re.compile(r"(?i)^communication[\s\-]?style\s*[:=\-]\s*")),
]

# Profile ist ein mehrzeiliges Feld (letztes vor <<END>>)
_PROFILE_RE = re.compile(r"(?i)^profile\s*[:=\-]\s*")

# Werte die beim Merge ignoriert werden (LLM-Platzhalter fuer "weiss nicht")
_SKIP_VALUES = frozenset({
    "nicht bestimmbar", "nicht beurteilbar", "unbekannt",
    "keine neuen informationen", "n/a", "-", "\u2014",
})


def _parse_contact_block(text: str) -> dict:
    """
    Parst einen <<BEGIN>>...<<END>> Block in ein dict.
    Profile ist mehrzeilig: alles nach 'Profile:' bis <<END>>.
    """
    # Block zwischen Markern extrahieren (Fallback: ganzer Text)
    begin = _BEGIN_RE.search(text)
    end = _END_RE.search(text)
    if begin and end and end.start() > begin.end():
        text = text[begin.end():end.start()]
    elif begin:
        text = text[begin.end():]

    out = {}
    profile_lines = []
    in_profile = False

    for line in text.splitlines():
        stripped = line.strip()

        # Wenn wir im Profile-Modus sind: pruefen ob ein neues Label kommt
        if in_profile:
            label_match = False
            for _, regex in _CONTACT_LABELS:
                if regex.match(stripped):
                    label_match = True
                    break
            if label_match or _END_RE.match(stripped):
                # Profile beenden, diese Zeile als normales Label weiterverarbeiten
                in_profile = False
            else:
                # Zeile zum Profile hinzufuegen (Leerzeilen erhalten)
                profile_lines.append(line.rstrip())
                continue

        if not stripped:
            continue

        # Profile-Start erkennen
        m = _PROFILE_RE.match(stripped)
        if m:
            in_profile = True
            rest = stripped[m.end():].strip()
            if rest:
                profile_lines.append(rest)
            continue

        # Einzeilige Felder
        for key, regex in _CONTACT_LABELS:
            m = regex.match(stripped)
            if m:
                val = stripped[m.end():].strip().strip('"').strip("'")
                if val:
                    out[key] = val
                break

    # Profile zusammenfuegen
    profile_text = "\n".join(profile_lines).strip()
    if profile_text:
        out["profile"] = profile_text

    return out


def _format_email_section(thread: list[dict]) -> str:
    """Formatiert Thread-Kontext fuer den Contact-Prompt (max 3 neueste Mails)."""
    newest = thread[-1]
    subject = (newest.get("subject") or "").strip()

    recent = thread[-3:]
    if len(recent) == 1:
        header = f"Email subject: {subject}"
        header += f"\nFrom: {(newest.get('from') or '').strip()}"
        header += f"\nTo: {(newest.get('to') or '').strip()}"
        if newest.get("cc"):
            header += f"\nCc: {(newest.get('cc') or '').strip()}"
        return f"{header}\nEmail body:\n{(newest.get('body') or '')[:3000]}"

    parts = []
    for i, e in enumerate(recent, start=1):
        is_newest = (i == len(recent))
        label = f"--- Mail {i}/{len(recent)}"
        label += " [SENDER'S CURRENT MESSAGE] ---" if is_newest else " [EARLIER/QUOTED] ---"
        parts.append(label)
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


def extract_contact_info_via_llm(model: str, thread: list[dict], person: str,
                                 ollama_url: str, prompt_base: str = "",
                                 existing_contact: dict | None = None) -> dict:
    """
    Extrahiert Kontakt-Infos aus dem Thread per LLM.
    Gibt ein dict mit Feldern zurueck, bei Fehler leeres dict.
    """
    newest = thread[-1]
    sender = (newest.get("from") or "").strip()
    email_section = _format_email_section(thread)

    prompt = prompt_base.replace("{person}", person)

    # Bestehendes Profil + alle Felder mitgeben damit LLM bestehende Werte sehen kann
    if existing_contact:
        ep_lines = ["--- EXISTING PROFILE ---"]
        field_labels = [
            ("tone", "Tone"), ("language", "Language"),
            ("role_or_title", "Role"), ("relationship", "Relationship"),
            ("communication_style", "Communication-Style"),
        ]
        for key, label in field_labels:
            fld = existing_contact.get(key)
            if isinstance(fld, dict):
                val = (fld.get("value") or "").strip()
                if val:
                    ep_lines.append(f"{label}: {val}")
        pf = existing_contact.get("profile")
        if isinstance(pf, dict):
            pval = (pf.get("value") or "").strip()
            if pval:
                ep_lines.append(f"Profile:\n{pval}")
        ep_lines.append("--- END EXISTING PROFILE ---")
        if len(ep_lines) > 2:  # mehr als nur Rahmen
            prompt += "\n" + "\n".join(ep_lines) + "\n"

    prompt += f"\nThe sender is: {sender}\n\n{email_section}\n"

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

        extracted = _parse_contact_block(text)

        # Profil: Struktur-Sanitizing, Beleg-Strip, Bullet-Filter, Akkumulation
        prof = extracted.get("profile")
        if isinstance(prof, str) and prof.strip():
            old_profile = ""
            if existing_contact:
                pf = existing_contact.get("profile")
                if isinstance(pf, dict):
                    old_profile = (pf.get("value") or "").strip()
            result = _postprocess_profile(prof, old_profile or None,
                                          evidence_text=email_section)
            if result:
                extracted["profile"] = result
            else:
                extracted.pop("profile", None)

        return extracted

    except Exception as e:
        log.debug("Contact-LLM Fehler: %s", e)
        return {}
