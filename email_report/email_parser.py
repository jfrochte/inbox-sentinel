"""
email_parser.py – MIME-Parsing, Body-Extraktion und Reply/Forward-Splitting.

Dieses Modul ist ein Blattmodul ohne interne Paket-Abhaengigkeiten.
Es kuemmert sich um alles, was mit dem Parsen einzelner E-Mail-Nachrichten
zu tun hat: Header-Dekodierung, HTML-zu-Text-Konvertierung, Trennung von
neuestem Inhalt und zitierter Historie, sowie die Auswahl des besten
Body-Textes aus multipart-Mails.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import re
import email
import email.message
from email.header import decode_header
from email.utils import parseaddr

from bs4 import BeautifulSoup
from bs4 import FeatureNotFound


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
