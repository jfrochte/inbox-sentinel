import imaplib
import email
import os
import re
import html
import smtplib
import logging
from datetime import datetime, timedelta, date
from email.header import decode_header
from email.utils import parseaddr
from getpass import getpass

import requests
from bs4 import BeautifulSoup
from bs4 import FeatureNotFound

# tqdm ist optional: wenn installiert, gibt es Fortschrittsbalken.
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ============================================================
# Logging (Punkt 8: weniger "Fehler schlucken" -> nachvollziehbar)
# ============================================================
# Ziel: Im Normalbetrieb nicht zu laut.
# Wenn du mehr sehen willst: setze ENV EMAIL_REPORT_LOGLEVEL=DEBUG
LOGLEVEL = os.environ.get("EMAIL_REPORT_LOGLEVEL", "INFO").upper().strip()
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("email_report")


# ============================================================
# Defaults (Entwicklung, wie gewünscht mit Olivia-Account)
# ============================================================
# IMAP:
DEFAULT_IMAP_SERVER = "mail.hs-bochum.de"
DEFAULT_IMAP_PORT = 993

# SMTP:
DEFAULT_SMTP_SERVER = "mail.hs-bochum.de"
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SSL = False  # bei 465 typischerweise True

# Account Defaults
DEFAULT_FROM_EMAIL = "joerg.frochte@hs-bochum.de"
DEFAULT_RECIPIENT_EMAIL = "joerg.frochte@hs-bochum.de"
DEFAULT_USERNAME = "j10f01191"
DEFAULT_NAME = "Jörg Frochte"

# Unsicher, aber ausdrücklich gewünscht für Entwicklung:
# Tipp: du kannst das per ENV überschreiben, ohne Code zu ändern:
#   export DEV_EMAIL_PASSWORD='...'
#DEFAULT_PASSWORD = ""

# LLM/Ollama Defaults
DEFAULT_MODEL = "qwen2.5:7b-instruct-q8_0"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"

# Funktions-Defaults
DEFAULT_MAILBOX = "INBOX"
SKIP_OWN_SENT_MAILS = True

# Punkt 6: IMAP Datumsfilter
# - SINCE/BEFORE: serverseitige INTERNALDATE (Zustell-/Ablagezeit)
# - SENTSINCE/SENTBEFORE: "Date:" Header (Sendedatum) laut RFC-Header
# Beides kann je nach Server/Zeitzone unterschiedlich wirken.
# Für "ich will die Mails, die an diesem Tag gesendet wurden" ist SENT* oft näher dran.
USE_SENTDATE_SEARCH = True

# Report Dateien
REPORT_DIR = "."
DEBUG_KEEP_FILES = os.environ.get("EMAIL_REPORT_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")


# ============================================================
# Hilfsfunktionen: Prompts am Anfang (Enter -> Default)
# ============================================================
def prompt_with_default(label: str, default: str) -> str:
    """
    Fragt einen String ab.
    - Return druecken => Default
    - Leerzeichen werden getrimmt
    """
    val = input(f"{label} [{default}]: ").strip()
    return val if val else default


def prompt_int_with_default(label: str, default: int) -> int:
    """
    Fragt eine Ganzzahl ab.
    - Return => Default
    - Bei falscher Eingabe wird wiederholt gefragt.
    """
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            return int(raw)
        print("Bitte eine ganze Zahl eingeben (oder Return fuer Default).")


def prompt_bool_with_default(label: str, default: bool) -> bool:
    """
    Boolean Prompt:
    - y/yes/1/true/on => True
    - n/no/0/false/off => False
    - Return => Default
    """
    d = "y" if default else "n"
    raw = input(f"{label} [y/n, Default {d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "on")


def prompt_secret_with_default(label: str) -> str:
    """
    Passwort per getpass:
    - Enter => Default Passwort (wie gewuenscht fuer Dev)
    Hinweis: Wir zeigen das Default nicht an, aber Enter nimmt es.
    """
    raw = getpass(f"{label} : ")
    return raw

def load_prompt_file(path: str) -> str:
    """
    Laedt einen Prompt aus einer Textdatei.
    - UTF-8
    - Entfernt ein evtl. UTF-8 BOM
    - Stellt sicher, dass der Prompt mit einem Newline endet
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        txt = f.read()

    txt = txt.strip()
    if not txt.endswith("\n"):
        txt += "\n"
    return txt



# ============================================================
# Hilfsfunktionen: sichere Dateibehandlung (mit Logging)
# ============================================================
def safe_remove(path: str) -> None:
    """
    Loescht Datei, falls vorhanden.
    Punkt 8: Nicht komplett still sein. Wir loggen bei DEBUG.
    """
    try:
        os.remove(path)
        log.debug("Removed file: %s", path)
    except FileNotFoundError:
        log.debug("File not found (ok): %s", path)
    except Exception as e:
        log.debug("Could not remove %s: %s", path, e)


def ensure_mode_0600(path: str) -> None:
    """
    Setzt best effort Dateirechte auf 0600.
    Unter Windows oder manchen Mounts kann das wirkungslos sein.
    """
    try:
        os.chmod(path, 0o600)
    except Exception as e:
        log.debug("chmod(0600) failed for %s: %s", path, e)


def append_secure(path: str, text: str) -> None:
    """
    Haengt Text an Datei an, best effort 0600.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(text)
    finally:
        ensure_mode_0600(path)


def write_secure(path: str, text: str) -> None:
    """
    Ueberschreibt Datei, best effort 0600.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    finally:
        ensure_mode_0600(path)


# ============================================================
# Hilfsfunktionen: E-Mail Parsing
# ============================================================
def decode_mime_words(s):
    """
    Dekodiert MIME-encoded Header wie Subject/From.
    Robust gegen unbekannte Encodings durch fallback auf utf-8.
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
    Extrahiert die reine E-Mail-Adresse aus einem Headerfeld.
    """
    _name, addr = parseaddr(header_value or "")
    return (addr or "").strip()


def html_to_text(html_str: str) -> str:
    """
    HTML -> Text (BeautifulSoup).
    Wir normalisieren Umbrueche und reduzieren sehr viele Leerzeilen.
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
# Punkt 4: bessere "neuster Teil vs Historie" Behandlung
# ============================================================
RE_REPLY_MARKERS = [
    # typische Outlook/Thunderbird Trenner
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Ursprüngliche Nachricht\s*-{2,}\s*$", re.I),

    # "On ... wrote:" (EN)
    re.compile(r"^\s*On .+ wrote:\s*$", re.I),

    # "Am ... schrieb ...:" (DE)
    re.compile(r"^\s*Am .+ schrieb .+:\s*$", re.I),

    # Headerblock in Replies/Forwards (DE/EN)
    # Wir versuchen hier bewusst nicht jede einzelne Headerzeile zu matchen,
    # sondern einen "Blockstart" zu erkennen, der oft mit "From:" oder "Von:" beginnt.
    re.compile(r"^\s*(From|Von)\s*:\s*.+$", re.I),
]


def split_newest_and_history(text: str):
    """
    Trennt den "neuesten" Teil einer Mail von der zitierten Historie.

    Heuristik:
    - Wir suchen die erste Zeile, die wie ein Reply/Forward-Marker aussieht.
    - Alles davor gilt als neuer Teil.
    - Alles ab Marker gilt als Historie.

    Vorteil:
    - LLM sieht oben die aktuelle Nachricht klarer.
    Nachteil:
    - Heuristik kann mal zu frueh schneiden, je nach Inhalt.
    """
    if not text:
        return "", ""

    lines = text.splitlines()
    cut_idx = None

    for i, line in enumerate(lines):
        # Wir schneiden erst, wenn:
        # - ein Marker matcht UND
        # - wir davor wenigstens ein bisschen Inhalt hatten (verhindert False Positive ganz oben)
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
    Zitiert Historie mit einfachem Prefix "> ".
    Wichtig: kein "Quote-Level hochzaehlen" mehr (alte Heuristik war oft zu aggressiv).
    """
    if not history:
        return ""
    return "\n".join(["> " + ln for ln in history.splitlines()]).strip()


# ============================================================
# Punkt 3: bessere Body-Auswahl (nicht einfach "erster Teil")
# ============================================================
def _score_candidate(text: str) -> int:
    """
    Bewertet einen Textkandidaten fuer "nutzbarer Body".
    Sehr einfache Scoring-Idee:
    - viele Nicht-Whitespace Zeichen => gut
    - sehr hoher Anteil gequoteter Zeilen (>) => eher Historie => schlechter
    - extrem kurze Texte => schlecht
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

    # Basis: Anzahl der "sichtbaren" Zeichen
    base = sum(1 for ch in stripped if not ch.isspace())

    # Quote-Anteil bestraft den Score, weil wir den neuen Inhalt bevorzugen wollen
    penalty = int(base * 0.6 * quote_ratio)

    return max(0, base - penalty)


def extract_best_body_text(message: email.message.Message) -> str:
    """
    Extrahiert einen Body-Text pro Mail, aber robuster:

    1) Wir sammeln Kandidaten aus allen text/plain und text/html Teilen.
       - Attachments werden ignoriert (Content-Disposition == attachment)
       - zusaetzlich ignorieren wir Parts mit Filename (oft "inline attachment")
    2) HTML Kandidaten werden zu Text konvertiert.
    3) Wir scoren Kandidaten und nehmen den besten.
       Das ist besser als "immer den ersten nehmen", weil:
       - multipart/alternative oft mehrere Varianten hat
       - einige plain Teile sind nur Stub ("open in browser")
    4) Danach splitten wir in newest/history und zitieren die Historie sauber.
    """
    candidates = []  # List[tuple(score, text, ctype)]

    def add_candidate(raw_text: str, ctype: str):
        if not raw_text:
            return
        newest, history = split_newest_and_history(raw_text)
        # Wir bauen finalen Body so, dass neuer Teil oben steht.
        # Historie wird zitiert, aber nicht eskalierend.
        if history:
            merged = newest + "\n\nQuoted history (trimmed):\n" + quote_history_block(history)
        else:
            merged = newest

        score = _score_candidate(merged)
        candidates.append((score, merged, ctype))

    # Multipart: iteriere ueber alle Parts
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue

            ctype = part.get_content_type()
            disp = part.get_content_disposition()  # None, 'inline', 'attachment'
            filename = part.get_filename()

            # Attachments skippen
            if disp == "attachment":
                continue

            # Viele Systeme setzen Content-Disposition nicht sauber, aber geben filename mit.
            # Wenn filename gesetzt ist, ist es sehr oft kein "eigentliches Body-Fragment".
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
        # Singlepart
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

    # Hoechster Score gewinnt.
    # Bei Gleichstand ist Reihenfolge egal, wir nehmen einfach max.
    best = max(candidates, key=lambda x: x[0])
    return best[1].strip()


# ============================================================
# IMAP Abruf (Punkt 6: optional SENT* Suche)
# ============================================================
def imap_fetch_emails_for_range(username: str, password: str, from_email: str, days_back: int,
                              imap_server: str, imap_port: int, mailbox: str,
                              use_sentdate: bool):
    """
    Liefert Liste von dicts:
    {
      'subject': ...,
      'from': ...,
      'from_addr': ...,
      'to': ...,
      'cc': ...,
      'body': ...,
    }

    Der Zeitraum wird als [heute - days_back, morgen) definiert, also inklusiv heute und der letzten days_back Tage.

    Suchlogik:
    - use_sentdate True: (SENTSINCE <start> SENTBEFORE <end_excl>)
      basiert auf "Date:" Header (Sendedatum)
    - use_sentdate False: (SINCE <start> BEFORE <end_excl>)
      basiert auf INTERNALDATE (Ablagezeit)
    """
    # Zeitraum:
    # days_back = 0  -> nur heute
    # days_back = 2  -> heute + die letzten 2 Tage (insgesamt 3 Kalendertage)
    start_day = (datetime.now() - timedelta(days=days_back)).date()      # inklusiv
    end_day_excl = datetime.now().date() + timedelta(days=1)            # exklusiv (morgen)

    since_str = start_day.strftime("%d-%b-%Y")
    before_str = end_day_excl.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    emails = []

    try:
        mail.login(username, password)

        # read-only, damit "Seen" nicht gesetzt wird (sofern Server das respektiert)
        try:
            mail.select(mailbox, readonly=True)
        except Exception:
            mail.select(mailbox)

        if use_sentdate:
            # SENTSINCE/SENTBEFORE sind standardisiert, aber nicht jeder Server ist 100% kompatibel.
            query = f"(SENTSINCE {since_str} SENTBEFORE {before_str})"
        else:
            query = f"(SINCE {since_str} BEFORE {before_str})"

        status, data = mail.search(None, query)
        if status != "OK":
            log.warning("IMAP search failed. status=%s query=%s", status, query)
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        iterator = msg_ids
        if tqdm is not None:
            iterator = tqdm(msg_ids, desc="Download E-Mails")

        for msg_id in iterator:
            typ, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK":
                continue

            raw_bytes = None
            for part in msg_data:
                if isinstance(part, tuple):
                    raw_bytes = part[1]
                    break
            if not raw_bytes:
                continue

            message = email.message_from_bytes(raw_bytes)

            from_header = decode_mime_words(message.get("from"))
            from_addr = get_email_address_from_header(from_header).lower()

            if SKIP_OWN_SENT_MAILS and from_addr and from_addr == from_email.lower():
                continue

            subject = decode_mime_words(message.get("subject"))
            to_header = decode_mime_words(message.get("to"))
            cc_header = decode_mime_words(message.get("cc"))

            body = extract_best_body_text(message)

            emails.append({
                "subject": subject,
                "from": from_header,
                "from_addr": from_addr,
                "to": to_header,
                "cc": cc_header,
                "body": body,
            })

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return emails


# ============================================================
# LLM Analyse via Ollama
# ============================================================

def analyze_email_via_ollama(model: str, email_text: str, person: str, ollama_url: str, prompt_base: str) -> str:
    headers = {"Content-Type": "application/json"}

    # Platzhalter ersetzen (falls im prompt.txt vorhanden)
    base = prompt_base.format(person=person)

    prompt = (
        base
        + "\n--- EMAIL START ---\n"
        + email_text
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 8192,
            "temperature": 0.3,
            "top_p": 0.2,
            "top_k": 40,
            "num_predict": 250,
        },
    }

    try:
        resp = requests.post(ollama_url, json=payload, headers=headers, timeout=180)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        return (
            "Subject: (Error)\n"
            "Sender: (Error)\n"
            f"Summary: HTTP error {resp.status_code}: {resp.text}\n"
            "Priority: 5\n"
            f"Actions for {person}: None\n"
        )
    except Exception as e:
        return (
            "Subject: (Error)\n"
            "Sender: (Error)\n"
            f"Summary: Exception while calling LLM: {e}\n"
            "Priority: 5\n"
            f"Actions for {person}: None\n"
        )


# ============================================================
# Prioritaet extrahieren und sortieren
# (bleibt bewusst tolerant)
# ============================================================
# ============================================================
# Punkt 1-4: Coverage-Garantie, Validator, Repair-Pass, Excerpt
# ============================================================
ALLOWED_ADDRESSING = {"DIRECT", "CC", "GROUP", "LIST", "UNKNOWN"}
ALLOWED_ASKED = {"YES", "NO"}
ALLOWED_STATUS = {"OK", "REPAIRED", "FALLBACK"}

def _extract_marked_block(text: str, begin: str = "<<BEGIN>>", end: str = "<<END>>") -> str:
    """Extrahiert den Block zwischen Markern. Wenn Marker fehlen, gibt den Originaltext zurueck."""
    if not text:
        return ""
    t = text
    b = t.find(begin)
    e = t.find(end)
    if b != -1 and e != -1 and e > b:
        inner = t[b + len(begin):e]
        return inner.strip()
    return t.strip()


def _compact_excerpt(body: str, limit: int = 450) -> str:
    """Kurzer Auszug fuer Debug/Fallback im Report."""
    txt = (body or "").strip()
    if not txt:
        return ""
    txt = re.sub(r"\s+", " ", txt)
    return (txt[:limit] + ("..." if len(txt) > limit else "")).strip()


def _make_fallback_block(email_obj: dict, person: str, reason: str) -> str:
    subj = (email_obj.get("subject") or "").strip() or "(ohne Betreff)"
    sender = (email_obj.get("from") or "").strip() or "(unbekannt)"
    excerpt = _compact_excerpt(email_obj.get("body") or "")
    # Priority bewusst 2: soll sichtbar sein, aber nicht wie Alarmstufe 1.
    block = (
        f"Subject: {subj}\n"
        f"Sender: {sender}\n"
        f"Context: \n"
        f"Addressing: UNKNOWN\n"
        f"Asked-Directly: NO\n"
        f"Priority: 2\n"
        f"LLM-Status: FALLBACK\n"
        f"Actions for {person}: Originalmail oeffnen und pruefen\n"
        f"Summary: LLM Output unbrauchbar ({reason}). Bitte Original-Mail pruefen.\n"
    )
    if excerpt:
        block += f"Raw-Excerpt: {excerpt}\n"
    return block


def _validate_parsed_summary(d):
    errs = []
    try:
        p = int(d.get("priority", 5))
    except Exception:
        p = 5
    if not (1 <= p <= 5):
        errs.append("priority")

    addressing = (d.get("addressing") or "").strip().upper()
    if addressing and addressing not in ALLOWED_ADDRESSING:
        d["addressing"] = "UNKNOWN"

    asked = (d.get("asked") or "").strip().upper()
    if asked and asked not in ALLOWED_ASKED:
        d["asked"] = "NO"

    summary = (d.get("summary") or "").strip()
    if not summary:
        errs.append("summary_empty")

    return (len(errs) == 0), errs


def _canonical_block_from_parsed(parsed: dict, email_obj: dict, person: str, status: str = "OK", raw_excerpt: str = "") -> str:
    """Erzeugt einen sauberen Block (ein Label pro Zeile), egal wie das LLM antwortet."""
    subj = (parsed.get("subject") or "").strip() or (email_obj.get("subject") or "").strip() or "(ohne Betreff)"
    sender = (parsed.get("sender") or "").strip() or (email_obj.get("from") or "").strip() or "(unbekannt)"

    addressing = (parsed.get("addressing") or "UNKNOWN").strip().upper()
    if addressing not in ALLOWED_ADDRESSING:
        addressing = "UNKNOWN"

    asked = (parsed.get("asked") or "NO").strip().upper()
    if asked not in ALLOWED_ASKED:
        asked = "NO"

    try:
        prio = int(parsed.get("priority", 5))
    except Exception:
        prio = 5
    if prio < 1 or prio > 5:
        prio = 5

    context = (parsed.get("context") or "").strip()
    actions = (parsed.get("actions") or "").strip() or "Keine."
    summary = (parsed.get("summary") or "").strip() or "Unklar. Bitte Original-Mail pruefen."

    # Excerpt nur bei Bedarf: Repair/Fallback oder P1/P2
    show_excerpt = bool(raw_excerpt) and (status in ("REPAIRED", "FALLBACK") or prio in (1, 2))

    block = (
        f"Subject: {subj}\n"
        f"Sender: {sender}\n"
        f"Context: {context}\n"
        f"Addressing: {addressing}\n"
        f"Asked-Directly: {asked}\n"
        f"Priority: {prio}\n"
        f"LLM-Status: {status}\n"
        f"Actions for {person}: {actions}\n"
        f"Summary: {summary}\n"
    )
    if show_excerpt:
        block += f"Raw-Excerpt: {raw_excerpt}\n"
    return block


def _ollama_generate(model: str, prompt: str, ollama_url: str, num_predict: int = 320, temperature: float = 0.2) -> str:
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 8192,
            "temperature": temperature,
            "top_p": 0.2,
            "top_k": 40,
            "num_predict": num_predict,
        },
    }
    resp = requests.post(ollama_url, json=payload, headers=headers, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return (resp.json().get("response") or "").strip()


def _repair_summary_via_ollama(model: str, person: str, email_text: str, broken_output: str, ollama_url: str) -> str:
    """Zweiter Pass: nur Format-Repair. Sehr strikt, damit Parser wieder sauber arbeitet."""
    repair_prompt = (
        "Du bist ein strikter Formatter. Antworte NUR mit dem Block zwischen <<BEGIN>> und <<END>>.\n"
        "Keine Erklaerungen, kein Markdown, keine Zusatzzeilen.\n\n"
        "Format (alle Labels muessen genau einmal vorkommen):\n"
        f"<<BEGIN>>\n"
        "Subject: ...\n"
        "Sender: ...\n"
        "Context: ...\n"
        "Addressing: DIRECT | CC | GROUP | LIST | UNKNOWN\n"
        "Asked-Directly: YES | NO\n"
        "Priority: 1 | 2 | 3 | 4 | 5\n"
        f"Actions for {person}: ...\n"
        "Summary: ...\n"
        "<<END>>\n\n"
        "DEFAULTS wenn unklar: Addressing=UNKNOWN, Asked-Directly=NO, Priority=5, Actions=Keine., Summary=Unklar. Bitte Original-Mail pruefen.\n\n"
        "E-Mail (inklusive Historie als Kontext):\n"
        "-----\n"
        + email_text[:12000] +
        "\n-----\n\n"
        "Zu reparierender Modell-Output:\n"
        "-----\n"
        + (broken_output or "")[:6000] +
        "\n-----\n"
    )
    return _ollama_generate(model, repair_prompt, ollama_url, num_predict=260, temperature=0.0)


def _analyze_email_guaranteed(model: str, email_obj: dict, person: str, ollama_url: str, prompt_base: str) -> str:
    """Hauptlogik: garantiert pro E-Mail genau einen gueltigen Block."""
    # Mailtext fuer LLM: Header + Body
    mail_text = []
    mail_text.append(f"Subject: {email_obj.get('subject','')}")
    mail_text.append(f"From: {email_obj.get('from','')}")
    mail_text.append(f"To: {email_obj.get('to','')}")
    if email_obj.get("cc"):
        mail_text.append(f"Cc: {email_obj.get('cc','')}")
    mail_text.append("")
    mail_text.append(email_obj.get("body", "") or "")
    email_text = "\n".join(mail_text).strip()

    raw_excerpt = _compact_excerpt(email_obj.get("body") or "")

    # 1) erster Versuch
    try:
        out0 = analyze_email_via_ollama(model, email_text, person, ollama_url, prompt_base)
    except Exception as e:
        return _make_fallback_block(email_obj, person, f"Exception: {e}")

    if not (out0 or "").strip():
        return _make_fallback_block(email_obj, person, "leere Antwort")

    block0 = _extract_marked_block(out0)
    parsed0 = _parse_llm_summary_block(block0)
    ok0, _errs0 = _validate_parsed_summary(parsed0)

    if ok0:
        return _canonical_block_from_parsed(parsed0, email_obj, person, status="OK", raw_excerpt=raw_excerpt)

    # 2) Repair-Pass
    try:
        repaired = _repair_summary_via_ollama(model, person, email_text, out0, ollama_url)
        block1 = _extract_marked_block(repaired)
        parsed1 = _parse_llm_summary_block(block1)
        ok1, _errs1 = _validate_parsed_summary(parsed1)
        if ok1:
            return _canonical_block_from_parsed(parsed1, email_obj, person, status="REPAIRED", raw_excerpt=raw_excerpt)
    except Exception as e:
        log.debug("Repair-Pass fehlgeschlagen: %s", e)

    return _make_fallback_block(email_obj, person, "Repair gescheitert / unparsebar")

def extract_priority(text: str) -> int:
    """
    Tolerant: sucht irgendwo "Priority" und nimmt erste Ziffer.
    Das ist ok, weil du gesagt hast, es darf etwas "unsauber" sein.
    """
    for line in (text or "").splitlines():
        if "Priority" in line or "Priorität" in line:
            m = re.search(r"(\d)", line)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 5:
                    return val
    return 5


def sort_summaries_by_priority(input_filename: str, output_filename: str) -> None:
    """
    Splittet nach Trenner und sortiert nach Priority aufsteigend.
    """
    with open(input_filename, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.split("-----------------------") if b.strip()]
    items = [(b, extract_priority(b)) for b in blocks]
    items.sort(key=lambda x: x[1])

    out_parts = []
    for idx, (block, _prio) in enumerate(items, start=1):
        out_parts.append(f"E-Mail Nummer: {idx}\n{block}\n-----------------------\n")

    write_secure(output_filename, "".join(out_parts))


# ============================================================
# HTML Erstellung
# ============================================================
def summaries_to_html_pre(sorted_text: str) -> str:
    """
    Escape gegen HTML Injection und Ausgabe im <pre>.
    """
    escaped = html.escape(sorted_text)
    escaped = escaped.replace("-----------------------", "\n-----------------------\n")
    return (
        "<html><body>"
        "<p><b>Daily Email Report</b></p>"
        "<pre style=\"font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; white-space: pre-wrap;\">"
        + escaped +
        "</pre>"
        "</body></html>"
    )


def _parse_llm_summary_block(block: str) -> dict:
    """
    Parst einen LLM-Block (die Zeilen aus prompt.txt) in ein Dict.
    Robust gegen fehlende Felder und kleine Abweichungen im LLM-Output.

    Typische Probleme, die wir abfangen:
    - Mehrere Felder in einer Zeile (z.B. "Sender: ... | Addressing: ... | Asked: NO")
    - Labels auf Deutsch/Englisch (Betreff/Subject, Von/From/Sender, ...)
    - Actions/Summary als Abschnitt mit Folgelinien (Bulletpoints oder mehrere Zeilen)
    """
    out = {
        "subject": "",
        "sender": "",
        "context": "",
        "addressing": "UNKNOWN",
        "asked": "NO",
        "priority": 5,
        "status": "OK",
        "actions": "",
        "summary": "",
        "raw_excerpt": "",
    }

    # "E-Mail Nummer: X" rauswerfen (kommt aus sort_summaries_by_priority)
    lines = [ln.rstrip("\n") for ln in (block or "").splitlines()]
    lines = [ln for ln in lines if not ln.strip().startswith("E-Mail Nummer:")]

    # Label-Synonyme (case-insensitive)
    sep = r"\s*[:=\-]\s*"  # LLM macht manchmal ':' oder '-' oder '='

    label_regexes = [
        ("subject", re.compile(rf"(?i)\b(subject|betreff){sep}")),
        ("sender", re.compile(rf"(?i)\b(sender|from|von){sep}")),
        ("context", re.compile(rf"(?i)\b(context|kontext){sep}")),
        ("addressing", re.compile(rf"(?i)\b(addressing|adressierung|recipients|empf[aä]nger){sep}")),
        ("asked", re.compile(rf"(?i)\b(asked\s*directly|asked-directly|asked|direkt\s*angesprochen){sep}")),
        ("priority", re.compile(rf"(?i)\b(priority|priorit[aä]t|prio){sep}")),
        ("status", re.compile(rf"(?i)\b(llm\s*status|llm-status|status){sep}")),
        ("raw_excerpt", re.compile(rf"(?i)\b(raw\s*excerpt|raw-excerpt|excerpt|auszug){sep}")),
        ("summary", re.compile(rf"(?i)\b(summary|zusammenfassung){sep}")),
        # Actions for <Person> ist haeufig, aber variabel
        ("actions", re.compile(rf"(?i)\bactions\s*for\s+[^:=\-]{1,80}{sep}")),
        ("actions", re.compile(rf"(?i)\b(actions|action\s*items|todo|to-do|aufgaben){sep}")),
    ]

    def set_value(key: str, val: str) -> None:
        v = (val or "").strip()
        if not v:
            return

        if key == "priority":
            m = re.search(r"(\d)", v)
            if m:
                try:
                    p = int(m.group(1))
                    out["priority"] = p if 1 <= p <= 5 else 5
                except Exception:
                    out["priority"] = 5
            return

        if key == "asked":
            # Normalisieren, aber Originalinhalt nicht kaputt machen.
            vv = v.strip().upper()
            if vv in ("YES", "Y", "JA", "J"):
                out["asked"] = "YES"
            elif vv in ("NO", "N", "NEIN"):
                out["asked"] = "NO"
            else:
                out["asked"] = v
            return

        if key == "status":
            out["status"] = v.strip().upper()
            return

        if key in ("actions", "summary", "context", "raw_excerpt"):
            if out[key]:
                out[key] = (out[key] + "\n" + v).strip()
            else:
                out[key] = v
            return

        out[key] = v

    current_section = None  # "actions" oder "summary" oder "context" oder "raw_excerpt"

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Sonderfall: Abschnittsheader ohne klaren "Key: Value" (manche Modelle machen das)
        if re.match(r"(?i)^actions\b\s*([\-=]\s*)?$", line):
            current_section = "actions"
            continue
        if re.match(r"(?i)^(summary|zusammenfassung)\b\s*([\-=]\s*)?$", line):
            current_section = "summary"
            continue
        if re.match(r"(?i)^(context|kontext)\b\s*([\-=]\s*)?$", line):
            current_section = "context"
            continue
        if re.match(r"(?i)^(raw\s*excerpt|raw-excerpt|excerpt|auszug)\b\s*([\-=]\s*)?$", line):
            current_section = "raw_excerpt"
            continue

        # Mehrere Felder in einer Zeile: wir suchen alle Label-Starts und schneiden Values.
        hits = []
        for key, rx in label_regexes:
            for m in rx.finditer(line):
                hits.append((m.start(), m.end(), key))

        if hits:
            hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
            # Dubletten am selben Startpunkt entfernen (laengster Match gewinnt)
            dedup = []
            seen_starts = set()
            for s, e, k in hits:
                if s in seen_starts:
                    continue
                seen_starts.add(s)
                dedup.append((s, e, k))
            hits = sorted(dedup, key=lambda t: t[0])

            for i, (s, e, k) in enumerate(hits):
                end = hits[i + 1][0] if i + 1 < len(hits) else len(line)
                val = line[e:end].strip()
                # Entferne nur aeusserliche Trenner, nicht die innenliegenden.
                val = val.strip(" |\t")
                set_value(k, val)
                current_section = k if k in ("actions", "summary") else None
            continue

        # Standardfall: eine Zeile "Key: Value" oder eine Fortsetzungszeile.
        m = re.match(r"^([^:=\-]{2,60})\s*[:=\-]\s*(.*)$", line)
        if m:
            key_raw = m.group(1).strip()
            val = m.group(2).strip()

            # Key normalisieren
            k = key_raw.lower().strip()
            if k in ("subject", "betreff"):
                set_value("subject", val)
                current_section = None
            elif k in ("sender", "from", "von"):
                set_value("sender", val)
                current_section = None
            elif k in ("context", "kontext"):
                set_value("context", val)
                current_section = "context"
            elif k in ("addressing", "adressierung", "recipients", "empfänger", "empfaenger"):
                set_value("addressing", val)
                current_section = None
            elif k in ("asked-directly", "asked directly", "asked", "direkt angesprochen"):
                set_value("asked", val)
                current_section = None
            elif k in ("priority", "priorität", "prioritaet", "prio"):
                set_value("priority", val)
                current_section = None
            elif k in ("llm-status", "llm status", "status"):
                set_value("status", val)
                current_section = None
            elif k in ("raw-excerpt", "raw excerpt", "excerpt", "auszug"):
                set_value("raw_excerpt", val)
                current_section = "raw_excerpt"
            elif k.startswith("actions for") or k in ("actions", "action items", "todo", "to-do", "aufgaben"):
                set_value("actions", val)
                current_section = "actions"
            elif k in ("summary", "zusammenfassung"):
                set_value("summary", val)
                current_section = "summary"
            else:
                current_section = None
            continue

        # Fortsetzungszeile (typisch: Summary/Actions als Bulletpoints oder zweite Zeile)
        if current_section in ("actions", "summary", "context", "raw_excerpt"):
            set_value(current_section, line)

    return out


def _split_actions(actions_raw: str) -> list:
    """Robustes Splitten von Actions: ';' oder neue Zeilen oder Bulletpoints."""
    txt = (actions_raw or "").strip()
    if not txt:
        return []

    low = txt.strip().lower()
    if low in ("keine", "keine.", "none", "none.", "n/a", "na"):
        return []

    parts = re.split(r";|\n", txt)
    out = []
    for p in parts:
        s = (p or "").strip()
        s = re.sub(r"^[\-\*•\u2022\u00b7]+\s*", "", s)
        if s and s.lower() not in ("keine", "keine.", "none", "none."):
            out.append(s)
    return out


def summaries_to_html_cards(sorted_text: str, title: str = "Daily Email Report", expected_count=None) -> str:
    """
    Baut eine besser scanbare HTML-Mail (Kartenansicht).
    Hinweis: Der "Schnellblick" wurde bewusst entfernt.
    """
    blocks = [b.strip() for b in (sorted_text or "").split("-----------------------") if b.strip()]
    items = [_parse_llm_summary_block(b) for b in blocks]

    def esc(x: str) -> str:
        return html.escape(x or "")

    def esc_ml(x: str) -> str:
        """Escapen + Zeilenumbrueche in <br> wandeln (fuers Mail-HTML)."""
        return esc(x).replace("\n", "<br>")

    def prio_badge(priority: int) -> str:
        bg = {1: "#ffe5e5", 2: "#fff2dd", 3: "#fff9d6", 4: "#eef2ff", 5: "#f3f4f6"}.get(priority, "#f3f4f6")
        fg = {1: "#8a1f1f", 2: "#7a4a00", 3: "#6b5b00", 4: "#1f3a8a", 5: "#374151"}.get(priority, "#374151")
        return (
            f"<span style=\"display:inline-block;padding:2px 8px;border-radius:999px;"
            f"background:{bg};color:{fg};font-size:12px;font-weight:700;\">P{priority}</span>"
        )

    parts = []
    parts.append("<html><body style=\"margin:0;padding:0;background:#f5f6f7;\">")
    parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\">")
    parts.append("<tr><td align=\"center\" style=\"padding:16px;\">")
    parts.append("<table role=\"presentation\" width=\"680\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:680px;width:100%;\">")

    # Header
    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
    parts.append(f"<div style=\"font-size:20px;font-weight:800;color:#111827;\">{esc(title)}</div>")
    if items:
        reported = len(items)
        if expected_count is None:
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">Anzahl Mails: {reported}</div>")
        else:
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">Abgerufen: {expected_count} | Im Report: {reported}</div>")
            if expected_count != reported:
                parts.append("<div style=\"margin-top:10px;padding:10px;border-radius:10px;border:1px solid #fecaca;background:#fff1f2;color:#991b1b;font-size:12px;\"><b>WARNUNG:</b> Es fehlen Eintraege im Report. Bitte Log/Raw ansehen.</div>")
    parts.append("</td></tr>")
    parts.append("<tr><td style=\"height:12px;\"></td></tr>")

    if not items:
        parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
        parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
                     "style=\"background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;\">")
        parts.append("<tr><td style=\"padding:14px; color:#374151;\">Keine passenden Mails gefunden.</td></tr></table>")
        parts.append("</td></tr>")
        parts.append("</table></td></tr></table></body></html>")
        return "".join(parts)

    # Liste
    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
    parts.append("<div style=\"font-size:14px;font-weight:800;color:#111827;margin-bottom:8px;\">Mails</div>")
    parts.append("</td></tr>")

    for it in items:
        p = int(it.get("priority", 5) or 5)
        subj = esc(it.get("subject", ""))
        sender = esc(it.get("sender", ""))
        addressing = esc(it.get("addressing", "UNKNOWN"))
        asked = esc(it.get("asked", "NO"))
        summary = esc_ml(it.get("summary", ""))
        actions_raw = (it.get("actions") or "").strip()
        actions = _split_actions(actions_raw)
        context = (it.get("context") or "").strip()
        status = (it.get("status") or "OK").strip().upper()
        raw_excerpt = (it.get("raw_excerpt") or "").strip()

        parts.append("<tr><td style=\"padding-bottom:10px;\">")
        parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
                     "style=\"background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;\">")
        parts.append("<tr><td style=\"padding:12px 14px;\">")

        parts.append("<div style=\"display:flex;align-items:center;gap:10px;\">")
        parts.append(prio_badge(p))
        parts.append(f"<div style=\"font-size:15px;font-weight:800;color:#111827;\">{subj}</div>")
        parts.append("</div>")

        # Meta-Zeile (bewusst groesser, damit besser lesbar)
        parts.append(
            f"<div style=\"margin-top:6px;font-size:14px;line-height:1.4;color:#6b7280;\">"
            f"<b>Sender</b>: {sender}<br>"
            f"<b>Addressing</b>: {addressing} | <b>Asked</b>: {asked}"
            + (f" | <b>Status</b>: {esc(status)}" if status and status != "OK" else "")
            + f"</div>"
        )

        if summary:
            parts.append(f"<div style=\"margin-top:10px;color:#374151;line-height:1.45;\">{summary}</div>")

        if context:
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Kontext</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:10px;\">{esc_ml(context)}</div>")

        if actions and all(a.lower() not in ("keine.", "keine", "none", "none.") for a in actions):
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Actions</div>")
            parts.append("<ul style=\"margin:6px 0 0 18px;padding:0;color:#111827;\">")
            for a in actions:
                parts.append(f"<li style=\"margin:4px 0;line-height:1.35;\">{esc(a)}</li>")
            parts.append("</ul>")

        show_excerpt = bool(raw_excerpt) and (status in ("FALLBACK", "REPAIRED") or p in (1, 2))
        if show_excerpt:
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Excerpt</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;\">{esc_ml(raw_excerpt)}</div>")

        parts.append("</td></tr></table>")
        parts.append("</td></tr>")

    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;padding-top:8px;\">")
    parts.append("Hinweis: Inhalt wurde automatisch zusammengefasst. Bitte bei Unklarheiten die Original-Mail oeffnen.")
    parts.append("</td></tr>")

    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


def summaries_to_html(sorted_text: str, title: str = "Daily Email Report", expected_count=None) -> str:
    """
    Default: Kartenansicht.
    Fallback: setze ENV EMAIL_REPORT_HTML_PRE=1 fuer den alten <pre>-Output.
    """
    use_pre = os.environ.get("EMAIL_REPORT_HTML_PRE", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_pre:
        return summaries_to_html_pre(sorted_text)
    return summaries_to_html_cards(sorted_text, title=title, expected_count=expected_count)


# ============================================================
# SMTP Versand (Punkt 7: Envelope-From sauber + SSL/TLS 465)
# ============================================================
def send_email_html(username: str, password: str, from_email: str, recipient_email: str,
                    subject: str, html_content: str, plain_text: str,
                    smtp_server: str, smtp_port: int, smtp_ssl: bool) -> None:
    """
    Versand ueber SMTP.

    Punkt 7:
    - Wir setzen nicht nur Header "From", sondern auch Envelope-From explizit,
      damit SMTP-Server weniger "komisch" reagieren.
    - Bei 465 nutzen wir SMTP_SSL statt starttls().

    Zusaetzlich:
    - Multipart/alternative (text/plain + text/html), damit Mail-Clients sauber rendern.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    # Plain zuerst, dann HTML
    msg.attach(MIMEText(plain_text or "", "plain", "utf-8"))
    msg.attach(MIMEText(html_content or "", "html", "utf-8"))

    if smtp_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)

    try:
        server.ehlo()

        if not smtp_ssl:
            server.starttls()
            server.ehlo()

        server.login(username, password)

        server.send_message(msg, from_addr=from_email, to_addrs=[recipient_email])
        print("E-Mail wurde gesendet.")
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ============================================================
# Main (Punkt "alles am Anfang abfragen")
# ============================================================
def main():
    """
    Ablauf:
    1) Alle Parameter am Anfang abfragen (Return => Default)
    2) IMAP: Mails holen
    3) LLM Summaries
    4) Sortieren
    5) HTML Mail an sich selbst schicken
    6) Files loeschen (ausser Debug)
    """

    print("\nKonfiguration (Return nimmt jeweils Default):\n")

    prompt_file = prompt_with_default("Prompt-Datei", "prompt.txt")
    try:
        prompt_base = load_prompt_file(prompt_file)
    except FileNotFoundError:
        raise SystemExit(f"Prompt-Datei nicht gefunden: {prompt_file}")

    # Server und Ports
    imap_server = prompt_with_default("IMAP Server", DEFAULT_IMAP_SERVER)
    imap_port = prompt_int_with_default("IMAP Port", DEFAULT_IMAP_PORT)

    smtp_server = prompt_with_default("SMTP Server", DEFAULT_SMTP_SERVER)
    smtp_port = prompt_int_with_default("SMTP Port", DEFAULT_SMTP_PORT)
    smtp_ssl = prompt_bool_with_default("SMTP SSL/TLS verwenden", DEFAULT_SMTP_SSL)

    mailbox = prompt_with_default("Mailbox/Folder", DEFAULT_MAILBOX)

    # Account / Absender
    username = prompt_with_default("Username", DEFAULT_USERNAME)
    from_email = prompt_with_default("From E-Mail", DEFAULT_FROM_EMAIL)
    recipient_email = prompt_with_default("Recipient E-Mail", DEFAULT_RECIPIENT_EMAIL)
    person = prompt_with_default("Name", DEFAULT_NAME)

    # Datumsfenster
    days_back = prompt_int_with_default("Zeitraum in Tagen zurueck (0=heute, 2=heute+letzte 2 Tage)", 0)
    use_sentdate = prompt_bool_with_default("IMAP Suche ueber SENTDATE (Date: Header)", USE_SENTDATE_SEARCH)

    # Ollama
    model = prompt_with_default("Ollama Modell", DEFAULT_MODEL)
    ollama_url = prompt_with_default("Ollama URL", os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))

    print("\nKonfiguration (Achtung für Passwort KEIN Default):\n")

    # Passwort
    password = prompt_secret_with_default("Passwort")

    # Punkt 7: kleine Plausibilitaetswarnung
    # Viele Server erwarten, dass from_email in irgendeiner Form zur Auth passt.
    if "@" in username and from_email.lower() != username.lower():
        log.info("Hinweis: From E-Mail (%s) ist ungleich Username (%s). "
                 "Je nach SMTP-Policy kann das Probleme machen.", from_email, username)

    # IMAP: E-Mails holen
    emails = imap_fetch_emails_for_range(
        username=username,
        password=password,
        from_email=from_email,
        days_back=days_back,
        imap_server=imap_server,
        imap_port=imap_port,
        mailbox=mailbox,
        use_sentdate=use_sentdate,
    )
    if not emails:
        print("Keine E-Mails im gewaehlten Zeitraum gefunden.")
        return

    # Report-Dateien vorbereiten
    start_day = (datetime.now() - timedelta(days=days_back)).date()
    end_day = datetime.now().date()
    report_range = f"{start_day.isoformat()}_bis_{end_day.isoformat()}"
    os.makedirs(REPORT_DIR, exist_ok=True)

    summaries_file = os.path.join(REPORT_DIR, f"zusammenfassung_{report_range}.txt")
    sorted_file = os.path.join(REPORT_DIR, f"zusammenfassung-sortiert_{report_range}.txt")

    # Bei Nicht-Debug: vorhandene Dateien dieses Tages entfernen
    if not DEBUG_KEEP_FILES:
        safe_remove(summaries_file)
        safe_remove(sorted_file)

    iterator = emails
    if tqdm is not None:
        iterator = tqdm(emails, desc="Verarbeite E-Mails")

    # Verarbeitung: pro Mail LLM Summary erzeugen
    for e in iterator:
        # Mailtext fuer LLM: Header + Body
        mail_text = []
        mail_text.append(f"Subject: {e['subject']}")
        mail_text.append(f"From: {e['from']}")
        mail_text.append(f"To: {e['to']}")
        if e.get("cc"):
            mail_text.append(f"Cc: {e['cc']}")
        mail_text.append("")
        mail_text.append(e.get("body", ""))

        mail_text_for_llm = "\n".join(mail_text).strip()

        final_block = _analyze_email_guaranteed(model, e, person, ollama_url, prompt_base)

        append_secure(summaries_file, final_block)
        append_secure(summaries_file, "\n\n-----------------------\n\n")

    # Sortieren nach Priority
    sort_summaries_by_priority(summaries_file, sorted_file)

    # HTML Body bauen
    with open(sorted_file, "r", encoding="utf-8") as f:
        sorted_text = f.read()

    subject = f"Daily Email Report ({start_day.isoformat()} bis {end_day.isoformat()})"
    html_content = summaries_to_html(sorted_text, title=subject, expected_count=len(emails))

    # Versenden
    sent_ok = False
    try:
        send_email_html(
            username=username,
            password=password,
            from_email=from_email,
            recipient_email=recipient_email,
            subject=subject,
            html_content=html_content,
            plain_text=sorted_text,
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            smtp_ssl=smtp_ssl,
        )
        sent_ok = True
    finally:
        # Nach erfolgreichem Versand: Dateien loeschen, ausser Debug
        if sent_ok and (not DEBUG_KEEP_FILES):
            safe_remove(summaries_file)
            safe_remove(sorted_file)


if __name__ == "__main__":
    main()
