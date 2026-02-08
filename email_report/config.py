"""
config.py – Konfiguration, Defaults und Profilmanagement.

Dieses Modul ist ein Blattmodul ohne interne Paket-Abhaengigkeiten.
Es definiert die Config-Dataclass mit allen Parametern sowie Funktionen
zum Speichern, Laden, Auflisten und Loeschen von JSON-Profilen.

Passwort, debug_keep_files, debug_log und report_dir werden bewusst NICHT
in Profilen gespeichert – Passwort aus Sicherheitsgruenden, die Debug/Report-
Felder weil sie umgebungsspezifisch sind.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import os
import re
import json
from dataclasses import dataclass, field, asdict


# ============================================================
# Defaults
# ============================================================
# IMAP:
DEFAULT_IMAP_SERVER = ""
DEFAULT_IMAP_PORT = 993

# SMTP:
DEFAULT_SMTP_SERVER = ""
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SSL = False  # bei 465 typischerweise True

# Account Defaults
DEFAULT_FROM_EMAIL = ""
DEFAULT_RECIPIENT_EMAIL = ""
DEFAULT_USERNAME = ""
DEFAULT_NAME = ""

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
# Fuer "ich will die Mails, die an diesem Tag gesendet wurden" ist SENT* oft naeher dran.
USE_SENTDATE_SEARCH = True

# Auto-Sort: Zielordner fuer automatische E-Mail-Sortierung nach Kategorie
DEFAULT_SORT_FOLDERS = {"SPAM": "Spam", "PHISHING": "Quarantine", "FYI": "FYI"}

# Report Dateien
REPORT_DIR = "."
DEBUG_KEEP_FILES = os.environ.get("EMAIL_REPORT_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
DEBUG_LOG = os.environ.get("EMAIL_REPORT_DEBUG_LOG", "0").strip().lower() in ("1", "true", "yes", "on")
# Wenn Debug-Log aktiv ist, behalten wir Temp-Dateien automatisch (Debugging ohne Datenverlust).
DEBUG_KEEP_FILES = bool(DEBUG_KEEP_FILES or DEBUG_LOG)


# ============================================================
# Profilverzeichnis – liegt neben dem Paket (nicht innerhalb)
# ============================================================
_PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")

# Erlaubte Zeichen fuer Profilnamen: alphanumerisch, Unterstrich, Bindestrich
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# ============================================================
# Config Dataclass
# ============================================================
@dataclass
class Config:
    """Alle Parameter, die fuer einen Lauf benoetigt werden."""
    # Server
    imap_server: str = DEFAULT_IMAP_SERVER
    imap_port: int = DEFAULT_IMAP_PORT
    smtp_server: str = DEFAULT_SMTP_SERVER
    smtp_port: int = DEFAULT_SMTP_PORT
    smtp_ssl: bool = DEFAULT_SMTP_SSL

    # Organisation (Preset-Key, z.B. "hs-bochum", "gmail", "outlook" oder "")
    organization: str = ""

    # Account
    username: str = DEFAULT_USERNAME
    from_email: str = DEFAULT_FROM_EMAIL
    recipient_email: str = DEFAULT_RECIPIENT_EMAIL
    name: str = DEFAULT_NAME
    roles: str = ""

    # Mailbox
    mailbox: str = DEFAULT_MAILBOX
    skip_own_sent: bool = SKIP_OWN_SENT_MAILS
    use_sentdate: bool = USE_SENTDATE_SEARCH

    # Ollama / LLM
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_MODEL

    # Zeitraum
    days_back: int = 0

    # Prompt
    prompt_file: str = "prompt.txt"

    # Auto-Sort: E-Mails nach LLM-Kategorie in IMAP-Unterordner verschieben
    auto_sort: bool = True

    # Diese Felder werden NICHT in Profilen gespeichert:
    password: str = ""
    report_dir: str = REPORT_DIR
    debug_keep_files: bool = DEBUG_KEEP_FILES
    debug_log: bool = DEBUG_LOG

    # --------------------------------------------------------
    # Felder, die bei Profil-Export/-Import AUSGESCHLOSSEN werden.
    # Passwort: Sicherheit. Debug/Report: umgebungsspezifisch.
    # days_back: lauf-spezifisch, wird jedes Mal neu abgefragt.
    # --------------------------------------------------------
    _EXCLUDED_FROM_PROFILE = frozenset({"password", "debug_keep_files", "debug_log", "report_dir", "days_back"})

    def to_profile_dict(self) -> dict:
        """Gibt ein dict zurueck, das als JSON-Profil gespeichert werden kann."""
        d = asdict(self)
        for k in self._EXCLUDED_FROM_PROFILE:
            d.pop(k, None)
        return d

    @classmethod
    def from_profile_dict(cls, d: dict) -> "Config":
        """Erstellt eine Config aus einem Profil-dict (fehlende Felder = Defaults)."""
        # Nur bekannte Felder uebernehmen, unbekannte ignorieren
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known and k not in cls._EXCLUDED_FROM_PROFILE}
        return cls(**filtered)


# ============================================================
# Profil-Management (JSON-Dateien in profiles/)
# ============================================================
def _ensure_profiles_dir() -> str:
    """Erstellt das Profilverzeichnis falls noetig und gibt den Pfad zurueck."""
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    return _PROFILES_DIR


def _validate_profile_name(name: str) -> str:
    """Prueft und normalisiert den Profilnamen. Wirft ValueError bei ungueltigem Namen."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Profilname darf nicht leer sein.")
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(f"Ungueltiger Profilname '{name}': nur Buchstaben, Ziffern, _ und - erlaubt.")
    return name


def list_profiles() -> list[str]:
    """Gibt eine sortierte Liste der vorhandenen Profilnamen zurueck."""
    d = _PROFILES_DIR
    if not os.path.isdir(d):
        return []
    names = []
    for f in os.listdir(d):
        if f.endswith(".json"):
            names.append(f[:-5])
    return sorted(names, key=str.lower)


def load_profile(name: str) -> Config:
    """Laedt ein Profil und gibt eine Config zurueck."""
    name = _validate_profile_name(name)
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Profil '{name}' nicht gefunden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Config.from_profile_dict(data)


def save_profile(name: str, cfg: "Config") -> str:
    """Speichert ein Profil als JSON. Gibt den Dateipfad zurueck."""
    name = _validate_profile_name(name)
    d = _ensure_profiles_dir()
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_profile_dict(), f, ensure_ascii=False, indent=2)
    return path


def delete_profile(name: str) -> bool:
    """Loescht ein Profil. Gibt True zurueck wenn erfolgreich, False wenn nicht vorhanden."""
    name = _validate_profile_name(name)
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False
