"""
interactive.py – Alle Benutzer-Prompts und Profil-Auswahl-UI.

Abhaengigkeiten innerhalb des Pakets:
  - config (Config, list_profiles, load_profile, save_profile, Defaults)

Dieses Modul buendelt alle interaktiven Eingaben, die der Benutzer beim
Start des Programms machen muss. Es trennt die UI-Logik von der
eigentlichen Verarbeitung.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import os
from getpass import getpass

import requests

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.config import (
    Config,
    list_profiles,
    load_profile,
    save_profile,
    DEFAULT_IMAP_SERVER,
    DEFAULT_IMAP_PORT,
    DEFAULT_SMTP_SERVER,
    DEFAULT_SMTP_PORT,
    DEFAULT_SMTP_SSL,
    DEFAULT_FROM_EMAIL,
    DEFAULT_RECIPIENT_EMAIL,
    DEFAULT_USERNAME,
    DEFAULT_NAME,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_MAILBOX,
    USE_SENTDATE_SEARCH,
)
from email_report.organizations import ORGANIZATIONS, get_organization


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
    # Erst Environment Variable checken
    env_pw = os.environ.get("DEV_EMAIL_PASSWORD", "").strip()
    if env_pw:
        print(f"{label}: (aus Environment Variable)")
        return env_pw
    # Sonst interaktiv fragen
    return getpass(f"{label} : ")


# ============================================================
# Ollama: Modelle automatisch auflisten (nice UX)
# ============================================================
def _ollama_tags_url(ollama_url: str) -> str:
    """Leitet aus einer beliebigen Ollama-URL (z.B. /api/generate oder /api/chat) die /api/tags URL ab."""
    u = (ollama_url or "").strip()
    if not u:
        u = DEFAULT_OLLAMA_URL
    if "/api/" in u:
        base = u.split("/api/")[0].rstrip("/")
        return base + "/api/tags"
    return u.rstrip("/") + "/api/tags"


def try_fetch_ollama_models(ollama_url: str, timeout_s: float = 4.0) -> list[str]:
    """Gibt eine Liste der lokal verfuegbaren Modelle zurueck. Bei Fehler: []."""
    tags_url = _ollama_tags_url(ollama_url)
    try:
        r = requests.get(tags_url, timeout=timeout_s)
        if r.status_code != 200:
            return []
        j = r.json()
        models = []
        for m in (j.get("models") or []):
            name = (m or {}).get("name")
            if name:
                models.append(name)
        models = sorted(set(models), key=lambda s: s.lower())
        return models
    except Exception:
        return []


def prompt_model_select(default_model: str, ollama_url: str) -> str:
    """Prompt, der erst versucht, Modelle via /api/tags zu listen. Auswahl per Nummer oder Name."""
    models = try_fetch_ollama_models(ollama_url)
    if not models:
        return prompt_with_default("Ollama Modell", default_model)

    print("\nVerfuegbare Ollama Modelle:")
    default_in_list = default_model in models
    for i, name in enumerate(models, 1):
        marker = " (default)" if name == default_model else ""
        print(f"  {i}) {name}{marker}")
    if not default_in_list:
        print(f"  Hinweis: Default '{default_model}' ist nicht in der Liste, du kannst ihn trotzdem eingeben.")

    raw = input(f"Ollama Modell (Nummer oder Name) [{default_model}]: ").strip()
    if not raw:
        return default_model

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(models):
            return models[idx - 1]
        print("Ungueltige Nummer. Nutze Default.")
        return default_model

    return raw


# ============================================================
# Profil-Auswahl beim Start
# ============================================================
def prompt_load_profile() -> tuple[Config | None, str]:
    """
    Zeigt verfuegbare Profile an und laesst den Benutzer eines waehlen.
    Gibt (Config, Profilname) zurueck oder (None, "") wenn kein Profil gewaehlt wurde.
    """
    profiles = list_profiles()
    if not profiles:
        return None, ""

    print("\nVerfuegbare Profile:")
    for i, name in enumerate(profiles, 1):
        print(f"  {i}) {name}")
    print(f"  0) Kein Profil laden (neues Profil erstellen)")

    raw = input("Profil waehlen (Nummer oder Name) [0]: ").strip()
    if not raw or raw == "0":
        return None, ""

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(profiles):
            name = profiles[idx - 1]
            cfg = load_profile(name)
            print(f"Profil '{name}' geladen.")
            return cfg, name
        print("Ungueltige Nummer. Kein Profil geladen.")
        return None, ""

    # Name direkt eingegeben
    if raw in profiles:
        cfg = load_profile(raw)
        print(f"Profil '{raw}' geladen.")
        return cfg, raw

    print(f"Profil '{raw}' nicht gefunden. Kein Profil geladen.")
    return None, ""


def prompt_save_profile(cfg: Config, default_name: str = "") -> None:
    """
    Fragt den Benutzer, ob die aktuelle Konfiguration als Profil gespeichert werden soll.
    Wenn default_name gesetzt ist, wird er als Vorschlag angeboten.
    """
    if default_name:
        raw = input(f"\nKonfiguration als Profil speichern? (Name) [{default_name}]: ").strip()
        if not raw:
            raw = default_name
    else:
        raw = input("\nKonfiguration als Profil speichern? (Name eingeben oder Return zum Ueberspringen): ").strip()
        if not raw:
            return

    try:
        path = save_profile(raw, cfg)
        print(f"Profil '{raw}' gespeichert: {path}")
    except ValueError as e:
        print(f"Fehler: {e}")


def prompt_all_settings(cfg: Config) -> Config:
    """
    Fragt alle Einstellungen interaktiv ab, wobei die Werte aus cfg als Defaults dienen.
    Gibt eine aktualisierte Config zurueck (Passwort wird hier NICHT abgefragt).
    """
    print("\nKonfiguration (Return nimmt jeweils Default):\n")

    prompt_file = prompt_with_default("Prompt-Datei", cfg.prompt_file)

    # Server und Ports
    imap_server = prompt_with_default("IMAP Server", cfg.imap_server)
    imap_port = prompt_int_with_default("IMAP Port", cfg.imap_port)

    smtp_server = prompt_with_default("SMTP Server", cfg.smtp_server)
    smtp_port = prompt_int_with_default("SMTP Port", cfg.smtp_port)
    smtp_ssl = prompt_bool_with_default("SMTP SSL/TLS verwenden", cfg.smtp_ssl)

    mailbox = prompt_with_default("Mailbox/Folder", cfg.mailbox)

    # Account / Absender
    username = prompt_with_default("Username", cfg.username)
    from_email = prompt_with_default("From E-Mail", cfg.from_email)
    recipient_email = prompt_with_default("Recipient E-Mail", cfg.recipient_email)
    name = prompt_with_default("Name", cfg.name)

    # Datumsfenster
    days_back = prompt_int_with_default("Zeitraum in Tagen zurueck (0=heute, 2=heute+letzte 2 Tage)", cfg.days_back)
    use_sentdate = prompt_bool_with_default("IMAP Suche ueber SENTDATE (Date: Header)", cfg.use_sentdate)

    # Ollama
    ollama_url = prompt_with_default("Ollama URL", os.environ.get("OLLAMA_URL", cfg.ollama_url))
    model = prompt_model_select(cfg.model, ollama_url)

    # Auto-Sort
    auto_sort = prompt_bool_with_default("Auto-Sort (Spam/Phishing/FYI in Unterordner verschieben)", cfg.auto_sort)

    # Aktualisierte Config zurueckgeben (Passwort und Debug-Felder bleiben unveraendert)
    cfg.prompt_file = prompt_file
    cfg.imap_server = imap_server
    cfg.imap_port = imap_port
    cfg.smtp_server = smtp_server
    cfg.smtp_port = smtp_port
    cfg.smtp_ssl = smtp_ssl
    cfg.mailbox = mailbox
    cfg.username = username
    cfg.from_email = from_email
    cfg.recipient_email = recipient_email
    cfg.name = name
    cfg.days_back = days_back
    cfg.use_sentdate = use_sentdate
    cfg.ollama_url = ollama_url
    cfg.model = model
    cfg.auto_sort = auto_sort

    return cfg


# ============================================================
# Profil-Zusammenfassung und Schnellstart (Flow A)
# ============================================================
def print_config_summary(cfg: Config) -> None:
    """Gibt eine kompakte Zusammenfassung der geladenen Config aus."""
    print("\n--- Aktuelle Konfiguration ---")
    print(f"  IMAP:       {cfg.imap_server}:{cfg.imap_port}")
    print(f"  SMTP:       {cfg.smtp_server}:{cfg.smtp_port} (SSL: {cfg.smtp_ssl})")
    print(f"  Username:   {cfg.username}")
    print(f"  Von:        {cfg.from_email}")
    print(f"  An:         {cfg.recipient_email}")
    print(f"  Name:       {cfg.name}")
    print(f"  Mailbox:    {cfg.mailbox}")
    print(f"  Zeitraum:   {cfg.days_back} Tage zurueck")
    print(f"  LLM:        {cfg.model}")
    print(f"  Ollama URL: {cfg.ollama_url}")
    print(f"  Prompt:     {cfg.prompt_file}")
    print(f"  Auto-Sort:  {cfg.auto_sort}")
    print("------------------------------")


def prompt_confirm_or_edit(cfg: Config) -> tuple[Config, bool]:
    """
    Zeigt die Config-Zusammenfassung und fragt, ob bearbeitet werden soll.
    Bei 'n' (Default) wird nur days_back abgefragt.
    Bei 'y' wird der volle Edit-Dialog gestartet.
    Gibt (Config, edited) zurueck – edited=True wenn der volle Dialog lief.
    """
    print_config_summary(cfg)
    edit = prompt_bool_with_default("Einstellungen bearbeiten?", False)
    if edit:
        cfg = prompt_all_settings(cfg)
        return cfg, True
    # Auch im Schnellstart: Zeitraum ist lauf-spezifisch
    cfg.days_back = prompt_int_with_default(
        "Zeitraum in Tagen zurueck (0=heute, 2=heute+letzte 2 Tage)", cfg.days_back)
    return cfg, False


# ============================================================
# Organisations-Auswahl und User-Settings (Flow B)
# ============================================================
def prompt_organization() -> dict | None:
    """
    Zeigt die verfuegbaren Organisations-Presets an.
    Gibt das gewaehlte Preset-Dict zurueck oder None fuer 'Eigener Server'.
    """
    print("\nOrganisation / E-Mail-Provider waehlen:")
    for i, org in enumerate(ORGANIZATIONS, 1):
        print(f"  {i}) {org['label']}")
    print(f"  0) Eigener Server (alle Felder manuell eingeben)")

    raw = input("Auswahl [0]: ").strip()
    if not raw or raw == "0":
        return None

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(ORGANIZATIONS):
            org = ORGANIZATIONS[idx - 1]
            print(f"Organisation '{org['label']}' gewaehlt.")
            return org
        print("Ungueltige Nummer. Eigener Server gewaehlt.")
        return None

    # Key direkt eingegeben
    org = get_organization(raw)
    if org:
        print(f"Organisation '{org['label']}' gewaehlt.")
        return org

    print(f"Organisation '{raw}' nicht gefunden. Eigener Server gewaehlt.")
    return None


def prompt_user_settings(cfg: Config) -> Config:
    """
    Fragt nur die benutzer-spezifischen Felder ab (Server sind durch Org-Preset gesetzt).
    """
    print("\nBenutzer-Einstellungen (Return nimmt jeweils Default):\n")

    cfg.username = prompt_with_default("Username", cfg.username)
    cfg.from_email = prompt_with_default("From E-Mail", cfg.from_email)
    cfg.recipient_email = prompt_with_default("Recipient E-Mail", cfg.recipient_email)
    cfg.name = prompt_with_default("Name", cfg.name)

    cfg.prompt_file = prompt_with_default("Prompt-Datei", cfg.prompt_file)
    cfg.days_back = prompt_int_with_default("Zeitraum in Tagen zurueck (0=heute, 2=heute+letzte 2 Tage)", cfg.days_back)

    ollama_url = prompt_with_default("Ollama URL", os.environ.get("OLLAMA_URL", cfg.ollama_url))
    cfg.ollama_url = ollama_url
    cfg.model = prompt_model_select(cfg.model, ollama_url)

    cfg.auto_sort = prompt_bool_with_default("Auto-Sort (Spam/Phishing/FYI in Unterordner verschieben)", cfg.auto_sort)

    return cfg
