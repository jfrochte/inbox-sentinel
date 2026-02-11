"""
vcard.py -- vCard 3.0 Reader/Writer fuer Kontakt-Dateien.

Blattmodul ohne Paket-Abhaengigkeiten (nur stdlib).
Unterstuetzt die Felder die inbox-sentinel braucht:
FN, N, NICKNAME, EMAIL, TEL, ADR, ORG, TITLE, ROLE, URL,
NOTE, BDAY, UID, REV, PRODID, CATEGORIES, TZ, GEO, SORT-STRING.

vCard 3.0 Spec: RFC 2426
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import re

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import write_secure


# ============================================================
# Konstanten
# ============================================================
_MULTI_VALUE_PROPS = frozenset({"TEL"})
_FOLD_LIMIT = 75


# ============================================================
# Escaping / Unescaping (vCard 3.0)
# ============================================================
def _unescape(value: str) -> str:
    """vCard value unescaping: \\n -> newline, \\, -> comma, \\; -> semicolon."""
    out = []
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == 'n' or nxt == 'N':
                out.append('\n')
            elif nxt == ',':
                out.append(',')
            elif nxt == ';':
                out.append(';')
            elif nxt == '\\':
                out.append('\\')
            else:
                out.append('\\')
                out.append(nxt)
            i += 2
        else:
            out.append(value[i])
            i += 1
    return ''.join(out)


def _escape(value: str) -> str:
    """vCard value escaping: newline -> \\n, comma -> \\,, semicolon -> \\;."""
    return (value
            .replace('\\', '\\\\')
            .replace(';', '\\;')
            .replace(',', '\\,')
            .replace('\n', '\\n'))


# ============================================================
# Line Folding / Unfolding
# ============================================================
def _unfold_lines(text: str) -> list[str]:
    """Unfold continuation lines (lines starting with space/tab are appended to previous)."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw and (raw[0] == ' ' or raw[0] == '\t'):
            if lines:
                lines[-1] += raw[1:]
            else:
                lines.append(raw[1:])
        else:
            lines.append(raw)
    return lines


def _fold_line(line: str) -> str:
    """Fold a single line at 75 octets (vCard 3.0 spec)."""
    encoded = line.encode('utf-8')
    if len(encoded) <= _FOLD_LIMIT:
        return line
    parts = []
    while len(encoded) > _FOLD_LIMIT:
        cut = _FOLD_LIMIT
        # Nicht mitten in einem UTF-8 Multi-Byte-Zeichen schneiden
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode('utf-8'))
        encoded = encoded[cut:]
    if encoded:
        parts.append(encoded.decode('utf-8'))
    return '\r\n '.join(parts)


# ============================================================
# N-Feld Parsing / Serializing
# ============================================================
def _parse_n_field(value: str) -> dict:
    """Parse N:Family;Given;Additional;Prefix;Suffix -> dict."""
    # Beachte: Semikolons in N sind structural, nicht escaped
    # Aber escaped semicolons muessen beruecksichtigt werden
    parts = _split_structured(value, 5)
    return {
        "family": parts[0],
        "given": parts[1],
        "additional": parts[2],
        "prefix": parts[3],
        "suffix": parts[4],
    }


def _serialize_n_field(n: dict) -> str:
    """Dict -> N value string: Family;Given;Additional;Prefix;Suffix."""
    if not n:
        return ";;;;"
    return ";".join([
        _escape(n.get("family") or ""),
        _escape(n.get("given") or ""),
        _escape(n.get("additional") or ""),
        _escape(n.get("prefix") or ""),
        _escape(n.get("suffix") or ""),
    ])


def _split_structured(value: str, expected: int) -> list[str]:
    """Split structured value on unescaped semicolons, unescape each part."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            current.append(value[i])
            current.append(value[i + 1])
            i += 2
        elif value[i] == ';':
            parts.append(_unescape(''.join(current)))
            current = []
            i += 1
        else:
            current.append(value[i])
            i += 1
    parts.append(_unescape(''.join(current)))
    # Pad to expected length
    while len(parts) < expected:
        parts.append("")
    return parts


# ============================================================
# Reader
# ============================================================
_PROP_RE = re.compile(r'^([A-Za-z0-9-]+)(?:;([^:]*))?\s*:\s*(.*)')


def read_vcard(filepath: str) -> dict | None:
    """
    Liest eine vCard-Datei und gibt ein dict zurueck.
    Gibt None zurueck wenn die Datei nicht existiert oder ungueltig ist.
    """
    import os
    if not os.path.isfile(filepath):
        return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return None

    lines = _unfold_lines(text)

    data: dict = {
        "FN": "", "N": {}, "NICKNAME": "", "EMAIL": "",
        "TEL": [], "ADR": "", "ORG": "", "TITLE": "", "ROLE": "",
        "URL": "", "NOTE": "", "BDAY": "", "UID": "", "REV": "",
        "PRODID": "", "CATEGORIES": "", "TZ": "", "GEO": "",
        "SORT-STRING": "",
    }

    in_vcard = False
    for line in lines:
        stripped = line.strip()

        if stripped.upper() == "BEGIN:VCARD":
            in_vcard = True
            continue
        if stripped.upper() == "END:VCARD":
            break
        if not in_vcard:
            continue

        m = _PROP_RE.match(stripped)
        if not m:
            continue

        prop_name = m.group(1).upper()
        # params = m.group(2)  # z.B. "TYPE=WORK" — aktuell nicht genutzt
        raw_value = m.group(3)

        if prop_name == "N":
            data["N"] = _parse_n_field(raw_value)
        elif prop_name == "CATEGORIES":
            # Kommas sind Trennzeichen in CATEGORIES, nicht escapen
            data["CATEGORIES"] = raw_value
        elif prop_name in _MULTI_VALUE_PROPS:
            data.setdefault(prop_name, [])
            if not isinstance(data[prop_name], list):
                data[prop_name] = [data[prop_name]] if data[prop_name] else []
            data[prop_name].append(_unescape(raw_value))
        elif prop_name == "NOTE":
            data["NOTE"] = _unescape(raw_value)
        elif prop_name in data:
            data[prop_name] = _unescape(raw_value)

    if not in_vcard:
        return None

    return data


# ============================================================
# Writer
# ============================================================
def write_vcard(filepath: str, data: dict) -> None:
    """Schreibt ein vCard-dict als .vcf Datei (0o600 Permissions)."""
    lines: list[str] = []
    lines.append("BEGIN:VCARD")
    lines.append("VERSION:3.0")

    # Reihenfolge gemaess vCard-Konvention
    if data.get("PRODID"):
        lines.append(_fold_line(f"PRODID:{_escape(data['PRODID'])}"))
    if data.get("UID"):
        lines.append(_fold_line(f"UID:{_escape(data['UID'])}"))

    # N (structured)
    n = data.get("N")
    if n and isinstance(n, dict):
        lines.append(_fold_line(f"N:{_serialize_n_field(n)}"))

    # FN
    if data.get("FN"):
        lines.append(_fold_line(f"FN:{_escape(data['FN'])}"))

    if data.get("NICKNAME"):
        lines.append(_fold_line(f"NICKNAME:{_escape(data['NICKNAME'])}"))

    if data.get("EMAIL"):
        lines.append(_fold_line(f"EMAIL:{_escape(data['EMAIL'])}"))

    # TEL (multi)
    tels = data.get("TEL") or []
    if isinstance(tels, str):
        tels = [tels] if tels else []
    for tel in tels:
        if tel:
            lines.append(_fold_line(f"TEL:{_escape(tel)}"))

    if data.get("ADR"):
        lines.append(_fold_line(f"ADR:{_escape(data['ADR'])}"))

    if data.get("ORG"):
        lines.append(_fold_line(f"ORG:{_escape(data['ORG'])}"))
    if data.get("TITLE"):
        lines.append(_fold_line(f"TITLE:{_escape(data['TITLE'])}"))
    if data.get("ROLE"):
        lines.append(_fold_line(f"ROLE:{_escape(data['ROLE'])}"))

    if data.get("URL"):
        lines.append(_fold_line(f"URL:{_escape(data['URL'])}"))
    if data.get("BDAY"):
        lines.append(_fold_line(f"BDAY:{_escape(data['BDAY'])}"))

    if data.get("CATEGORIES"):
        # CATEGORIES: Kommas sind Trennzeichen (nicht escapen)
        lines.append(_fold_line(f"CATEGORIES:{data['CATEGORIES']}"))
    if data.get("TZ"):
        lines.append(_fold_line(f"TZ:{_escape(data['TZ'])}"))
    if data.get("GEO"):
        lines.append(_fold_line(f"GEO:{_escape(data['GEO'])}"))
    if data.get("SORT-STRING"):
        lines.append(_fold_line(f"SORT-STRING:{_escape(data['SORT-STRING'])}"))

    if data.get("NOTE"):
        lines.append(_fold_line(f"NOTE:{_escape(data['NOTE'])}"))

    if data.get("REV"):
        lines.append(_fold_line(f"REV:{_escape(data['REV'])}"))

    lines.append("END:VCARD")

    write_secure(filepath, '\r\n'.join(lines) + '\r\n')
