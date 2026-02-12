"""
config.py -- Configuration, defaults, and profile management.

Leaf module with no internal package dependencies.
Defines the Config dataclass with all parameters, plus functions to
save, load, list, and delete JSON profiles.

Password, debug_keep_files, debug_log, and report_dir are deliberately
excluded from profiles -- password for security, debug/report fields
because they are environment-specific.
"""

# ============================================================
# External dependencies
# ============================================================
import os
import re
import json
from dataclasses import dataclass, field, asdict


# ============================================================
# Defaults
# ============================================================
DEFAULT_IMAP_SERVER = ""
DEFAULT_IMAP_PORT = 993

DEFAULT_SMTP_SERVER = ""
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SSL = False  # typically True for port 465

# Account defaults
DEFAULT_FROM_EMAIL = ""
DEFAULT_RECIPIENT_EMAIL = ""
DEFAULT_USERNAME = ""
DEFAULT_NAME = ""

# LLM / Ollama defaults
DEFAULT_MODEL = "gpt-os-20b"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"

DEFAULT_MAILBOX = "INBOX"
SKIP_OWN_SENT_MAILS = True

# IMAP date filter:
# - SINCE/BEFORE: server-side INTERNALDATE (delivery/storage time)
# - SENTSINCE/SENTBEFORE: "Date:" header (send date per RFC)
# Both can behave differently depending on server/timezone.
# For "emails sent on this day", SENT* is usually more accurate.
USE_SENTDATE_SEARCH = True

# Auto-triage: target folders for automatic email sorting by category
DEFAULT_SORT_FOLDERS = {"SPAM": "Spam", "PHISHING": "Quarantine"}

# IMAP keyword for crash-safe auto-triage (copy + tag instead of move)
SENTINEL_KEYWORD = "$Sentinel_Sorted"

# Report files
REPORT_DIR = "."
DEBUG_KEEP_FILES = os.environ.get("EMAIL_REPORT_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
DEBUG_LOG = os.environ.get("EMAIL_REPORT_DEBUG_LOG", "0").strip().lower() in ("1", "true", "yes", "on")
# When debug log is active, keep temp files automatically (debugging without data loss).
DEBUG_KEEP_FILES = bool(DEBUG_KEEP_FILES or DEBUG_LOG)


# ============================================================
# Profile directory -- lives next to the package (not inside)
# ============================================================
_PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# ============================================================
# Config dataclass
# ============================================================
@dataclass
class Config:
    """All parameters needed for a single run."""
    # Server
    imap_server: str = DEFAULT_IMAP_SERVER
    imap_port: int = DEFAULT_IMAP_PORT
    smtp_server: str = DEFAULT_SMTP_SERVER
    smtp_port: int = DEFAULT_SMTP_PORT
    smtp_ssl: bool = DEFAULT_SMTP_SSL

    # Organization preset key (e.g. "hs-bochum", "gmail", "outlook", or "")
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

    # Time range
    days_back: int = 0

    # Prompt
    prompt_file: str = "prompt.txt"

    # Auto-triage: move emails to IMAP subfolders based on LLM category
    auto_triage: bool = True

    # Auto-draft: generate LLM reply drafts for ACTIONABLE mails
    auto_draft: bool = False
    drafts_folder: str = "Drafts"
    signature_file: str = ""  # path to optional signature file for drafts

    # Auto-contacts: lazily build contact card for new senders
    auto_contacts_lazy: bool = False
    sent_folder: str = ""  # extra folder for contact material (e.g. "Sent")

    # These fields are NOT saved in profiles:
    password: str = ""
    report_dir: str = REPORT_DIR
    debug_keep_files: bool = DEBUG_KEEP_FILES
    debug_log: bool = DEBUG_LOG

    # --------------------------------------------------------
    # Fields excluded from profile export/import.
    # Password: security. Debug/report: environment-specific.
    # days_back: run-specific, prompted each time.
    # --------------------------------------------------------
    _EXCLUDED_FROM_PROFILE = frozenset({"password", "debug_keep_files", "debug_log", "report_dir", "days_back"})

    def to_profile_dict(self) -> dict:
        """Returns a dict suitable for saving as a JSON profile."""
        d = asdict(self)
        for k in self._EXCLUDED_FROM_PROFILE:
            d.pop(k, None)
        return d

    @classmethod
    def from_profile_dict(cls, d: dict) -> "Config":
        """Creates a Config from a profile dict (missing fields = defaults)."""
        # Only accept known fields, silently ignore unknown ones
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known and k not in cls._EXCLUDED_FROM_PROFILE}
        return cls(**filtered)


# ============================================================
# Profile management (JSON files in profiles/)
# ============================================================
def _ensure_profiles_dir() -> str:
    """Creates the profile directory if needed and returns the path."""
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    return _PROFILES_DIR


def _validate_profile_name(name: str) -> str:
    """Validates and normalizes the profile name. Raises ValueError on invalid names."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Profilname darf nicht leer sein.")
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(f"Ungueltiger Profilname '{name}': nur Buchstaben, Ziffern, _ und - erlaubt.")
    return name


def list_profiles() -> list[str]:
    """Returns a sorted list of existing profile names."""
    d = _PROFILES_DIR
    if not os.path.isdir(d):
        return []
    names = []
    for f in os.listdir(d):
        if f.endswith(".json"):
            names.append(f[:-5])
    return sorted(names, key=str.lower)


def load_profile(name: str) -> Config:
    """Loads a profile and returns a Config."""
    name = _validate_profile_name(name)
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Profil '{name}' nicht gefunden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Config.from_profile_dict(data)


def save_profile(name: str, cfg: "Config") -> str:
    """Saves a profile as JSON. Returns the file path."""
    name = _validate_profile_name(name)
    d = _ensure_profiles_dir()
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_profile_dict(), f, ensure_ascii=False, indent=2)
    return path


def delete_profile(name: str) -> bool:
    """Deletes a profile. Returns True on success, False if not found."""
    name = _validate_profile_name(name)
    path = os.path.join(_PROFILES_DIR, f"{name}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False
