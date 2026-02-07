"""
utils.py – Hilfsfunktionen fuer Dateioperationen, Logging und Textverarbeitung.

Dieses Modul ist ein Blattmodul ohne interne Paket-Abhaengigkeiten.
Es stellt grundlegende Werkzeuge bereit, die von mehreren anderen Modulen
des Pakets genutzt werden.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import os
import json
import logging


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


def write_jsonl(path: str, obj: dict) -> None:
    """Schreibt ein JSON-Objekt als eine Zeile (jsonl)."""
    try:
        line = json.dumps(obj, ensure_ascii=False)
    except Exception:
        line = json.dumps({"error": "could_not_serialize", "repr": repr(obj)}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
# Bug Fix: _tail_text war in der monolithischen Version aufgerufen
# (Zeile 953) aber nie definiert. Hier die korrekte Implementierung.
# ============================================================
def _tail_text(text: str, limit: int = 6000) -> str:
    """Gibt die letzten `limit` Zeichen von text zurueck."""
    if len(text) <= limit:
        return text
    return text[-limit:]
