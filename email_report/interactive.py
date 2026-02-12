"""
interactive.py -- User prompts and profile selection UI.

Dependencies within the package:
  - config (Config, list_profiles, load_profile, save_profile, defaults)
  - i18n (t for translated strings)

Bundles all interactive inputs the user needs to provide at startup.
Separates UI logic from actual processing.
"""

# ============================================================
# External dependencies
# ============================================================
import json
import os
from getpass import getpass

import requests

# ============================================================
# Internal package imports
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
from email_report.i18n import t


# ============================================================
# Helper functions: prompts with defaults (Enter -> default)
# ============================================================
def prompt_with_default(label: str, default: str, required: bool = False) -> str:
    """
    Prompts for a string value.
    - Enter => default
    - Whitespace is trimmed
    - required=True: repeats until non-empty input is given
    """
    while True:
        if default:
            val = input(f"{label} [{default}]: ").strip()
            return val if val else default
        else:
            val = input(f"{label}: ").strip()
            if val or not required:
                return val
            print(t("interactive.required_field"))


def prompt_int_with_default(label: str, default: int) -> int:
    """
    Prompts for an integer.
    - Enter => default
    - Repeats on invalid input.
    """
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            return int(raw)
        print(t("interactive.enter_integer"))


def prompt_bool_with_default(label: str, default: bool) -> bool:
    """
    Boolean prompt:
    - y/yes/1/true/on => True
    - n/no/0/false/off => False
    - Enter => default
    """
    d = "y" if default else "n"
    raw = input(f"{label} [y/n, Default {d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "on")


def prompt_secret_with_default(label: str) -> str:
    env_pw = os.environ.get("DEV_EMAIL_PASSWORD", "").strip()
    if env_pw:
        print(t("interactive.password_from_env", label=label))
        return env_pw
    return getpass(f"{label} : ")


# ============================================================
# Ollama: auto-list available models
# ============================================================
def _ollama_tags_url(ollama_url: str) -> str:
    """Derives the /api/tags URL from any Ollama URL (e.g. /api/generate or /api/chat)."""
    u = (ollama_url or "").strip()
    if not u:
        u = DEFAULT_OLLAMA_URL
    if "/api/" in u:
        base = u.split("/api/")[0].rstrip("/")
        return base + "/api/tags"
    return u.rstrip("/") + "/api/tags"


def try_fetch_ollama_models(ollama_url: str, timeout_s: float = 4.0) -> list[str]:
    """Returns a list of locally available models. On error: []."""
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
    """Model prompt: tries to list models via /api/tags first. Selection by number or name."""
    models = try_fetch_ollama_models(ollama_url)
    if not models:
        return prompt_with_default(t("interactive.label_ollama_url").split("(")[0].strip(), default_model)

    print(t("interactive.available_models"))
    default_in_list = default_model in models
    for i, name in enumerate(models, 1):
        marker = t("interactive.model_default_marker") if name == default_model else ""
        print(f"  {i}) {name}{marker}")
    if not default_in_list:
        print(t("interactive.model_not_in_list", model=default_model))

    raw = input(t("interactive.model_prompt", default=default_model)).strip()
    if not raw:
        return default_model

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(models):
            return models[idx - 1]
        print(t("interactive.invalid_number"))
        return default_model

    return raw


# ============================================================
# Profile selection at startup
# ============================================================
def prompt_load_profile() -> tuple[Config | None, str]:
    """
    Shows available profiles and lets the user choose one.
    Returns (Config, profile_name) or (None, "") if no profile was selected.
    """
    profiles = list_profiles()
    if not profiles:
        return None, ""

    print(t("interactive.available_profiles"))
    for i, name in enumerate(profiles, 1):
        print(f"  {i}) {name}")
    print(t("interactive.no_profile_option"))

    raw = input(t("interactive.select_profile")).strip()
    if not raw or raw == "0":
        return None, ""

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(profiles):
            name = profiles[idx - 1]
            cfg = load_profile(name)
            print(t("interactive.profile_loaded", name=name))
            return cfg, name
        print(t("interactive.invalid_number_no_profile"))
        return None, ""

    # Name entered directly
    if raw in profiles:
        cfg = load_profile(raw)
        print(t("interactive.profile_loaded", name=raw))
        return cfg, raw

    print(t("interactive.profile_not_found", name=raw))
    return None, ""


def prompt_save_profile(cfg: Config, default_name: str = "") -> None:
    """
    Asks the user whether to save the current configuration as a profile.
    If default_name is set, it is offered as suggestion.
    """
    if default_name:
        raw = input(t("interactive.save_profile_with_default", name=default_name)).strip()
        if not raw:
            raw = default_name
    else:
        raw = input(t("interactive.save_profile_prompt")).strip()
        if not raw:
            return

    try:
        path = save_profile(raw, cfg)
        print(t("interactive.profile_saved", name=raw, path=path))
    except ValueError as e:
        print(t("interactive.profile_save_error", error=str(e)))


def prompt_all_settings(cfg: Config) -> Config:
    """
    Prompts for all settings interactively, using values from cfg as defaults.
    Returns an updated Config (password is NOT prompted here).
    """
    print(t("interactive.config_header"))

    prompt_file = prompt_with_default(t("interactive.label_prompt_file"), cfg.prompt_file)

    # Server and ports
    imap_server = prompt_with_default(t("interactive.label_imap_server"), cfg.imap_server, required=True)
    imap_port = prompt_int_with_default(t("interactive.label_imap_port"), cfg.imap_port)

    smtp_server = prompt_with_default(t("interactive.label_smtp_server"), cfg.smtp_server, required=True)
    smtp_port = prompt_int_with_default(t("interactive.label_smtp_port"), cfg.smtp_port)
    smtp_ssl = prompt_bool_with_default(t("interactive.label_smtp_ssl"), cfg.smtp_ssl)

    mailbox = prompt_with_default(t("interactive.label_mailbox"), cfg.mailbox)

    # Account / sender
    username = prompt_with_default(t("interactive.label_username"), cfg.username, required=True)
    from_email = prompt_with_default(t("interactive.label_from_email"), cfg.from_email, required=True)
    recipient_email = prompt_with_default(t("interactive.label_recipient_email"), cfg.recipient_email, required=True)
    name = prompt_with_default(t("interactive.label_name"), cfg.name, required=True)
    roles = prompt_with_default(t("interactive.label_roles"), cfg.roles)

    # Date range
    days_back = prompt_int_with_default(t("interactive.label_days_back"), cfg.days_back)
    use_sentdate = prompt_bool_with_default(t("interactive.label_use_sentdate"), cfg.use_sentdate)

    # Language
    language = prompt_with_default(t("interactive.label_language"), cfg.language)

    # Ollama
    ollama_url = prompt_with_default(t("interactive.label_ollama_url"), os.environ.get("OLLAMA_URL", cfg.ollama_url))
    model = prompt_model_select(cfg.model, ollama_url)

    # Auto-Triage
    auto_triage = prompt_bool_with_default(t("interactive.label_auto_triage"), cfg.auto_triage)

    # Auto-Draft
    auto_draft = prompt_bool_with_default(t("interactive.label_auto_draft"), cfg.auto_draft)
    drafts_folder = cfg.drafts_folder
    if auto_draft:
        drafts_folder = prompt_with_default(t("interactive.label_drafts_folder"), cfg.drafts_folder)

    # Auto-Contacts (Lazy)
    auto_contacts_lazy = prompt_bool_with_default(t("interactive.label_auto_contacts"), cfg.auto_contacts_lazy)
    sent_folder = cfg.sent_folder
    if auto_contacts_lazy:
        sent_folder = prompt_with_default(t("interactive.label_sent_folder"), cfg.sent_folder)

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
    cfg.roles = roles
    cfg.days_back = days_back
    cfg.use_sentdate = use_sentdate
    cfg.language = language
    cfg.ollama_url = ollama_url
    cfg.model = model
    cfg.auto_triage = auto_triage
    cfg.auto_draft = auto_draft
    cfg.drafts_folder = drafts_folder
    cfg.auto_contacts_lazy = auto_contacts_lazy
    cfg.sent_folder = sent_folder

    return cfg


# ============================================================
# Profile summary and quick start (Flow A)
# ============================================================
def print_config_summary(cfg: Config) -> None:
    """Prints a compact summary of the loaded config."""
    print(t("interactive.config_summary_header"))
    print(t("interactive.summary_imap", server=cfg.imap_server, port=cfg.imap_port))
    print(t("interactive.summary_smtp", server=cfg.smtp_server, port=cfg.smtp_port, ssl=cfg.smtp_ssl))
    print(t("interactive.summary_username", username=cfg.username))
    print(t("interactive.summary_from", from_email=cfg.from_email))
    print(t("interactive.summary_to", recipient=cfg.recipient_email))
    print(t("interactive.summary_name", name=cfg.name))
    if cfg.roles:
        print(t("interactive.summary_roles", roles=cfg.roles))
    print(t("interactive.summary_mailbox", mailbox=cfg.mailbox))
    print(t("interactive.summary_days", days=cfg.days_back))
    print(t("interactive.summary_llm", model=cfg.model))
    print(t("interactive.summary_ollama", url=cfg.ollama_url))
    print(t("interactive.summary_prompt", prompt=cfg.prompt_file))
    print(t("interactive.summary_language", language=cfg.language))
    print(t("interactive.summary_triage", value=cfg.auto_triage))
    print(t("interactive.summary_draft", value=cfg.auto_draft))
    print(t("interactive.summary_contacts", value=cfg.auto_contacts_lazy))
    if cfg.auto_contacts_lazy and cfg.sent_folder:
        print(t("interactive.summary_sent_folder", value=cfg.sent_folder))
    print(t("interactive.config_summary_footer"))


def prompt_confirm_or_edit(cfg: Config) -> tuple[Config, bool]:
    """
    Shows config summary and asks whether to edit.
    On 'n' (default): only days_back is prompted.
    On 'y': full edit dialog runs.
    Returns (Config, edited) -- edited=True if full dialog was used.
    """
    print_config_summary(cfg)
    edit = prompt_bool_with_default(t("interactive.edit_settings"), False)
    if edit:
        cfg = prompt_all_settings(cfg)
        return cfg, True
    # Even in quick-start mode: time range is run-specific
    cfg.days_back = prompt_int_with_default(t("interactive.label_days_back"), cfg.days_back)
    return cfg, False


# ============================================================
# Organization presets (loaded from JSON data file)
# ============================================================
def _load_organizations() -> list[dict]:
    """Loads organization presets from organizations.json."""
    json_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "organizations.json",
    )
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ============================================================
# Organization selection and user settings (Flow B)
# ============================================================
def prompt_organization() -> dict | None:
    """
    Shows available organization presets.
    Returns the selected preset dict, or None for 'Custom server'.
    """
    orgs = _load_organizations()
    if not orgs:
        return None

    print(t("interactive.select_org"))
    for i, org in enumerate(orgs, 1):
        print(f"  {i}) {org['label']}")
    print(t("interactive.custom_server_option"))

    raw = input(t("interactive.select_org_prompt")).strip()
    if not raw or raw == "0":
        return None

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(orgs):
            org = orgs[idx - 1]
            print(t("interactive.org_selected", label=org['label']))
            return org
        print(t("interactive.invalid_org"))
        return None

    # Key entered directly
    for org in orgs:
        if org.get("key") == raw:
            print(t("interactive.org_selected", label=org['label']))
            return org

    print(t("interactive.org_not_found", name=raw))
    return None


def prompt_user_settings(cfg: Config) -> Config:
    """
    Prompts only for user-specific fields (server fields are set by org preset).
    """
    print(t("interactive.user_settings_header"))

    cfg.username = prompt_with_default(t("interactive.label_username"), cfg.username, required=True)
    cfg.from_email = prompt_with_default(t("interactive.label_from_email"), cfg.from_email, required=True)
    cfg.recipient_email = prompt_with_default(t("interactive.label_recipient_email"), cfg.recipient_email, required=True)
    cfg.name = prompt_with_default(t("interactive.label_name"), cfg.name, required=True)
    cfg.roles = prompt_with_default(t("interactive.label_roles"), cfg.roles)

    cfg.prompt_file = prompt_with_default(t("interactive.label_prompt_file"), cfg.prompt_file)
    cfg.days_back = prompt_int_with_default(t("interactive.label_days_back"), cfg.days_back)

    # Language
    cfg.language = prompt_with_default(t("interactive.label_language"), cfg.language)

    ollama_url = prompt_with_default(t("interactive.label_ollama_url"), os.environ.get("OLLAMA_URL", cfg.ollama_url))
    cfg.ollama_url = ollama_url
    cfg.model = prompt_model_select(cfg.model, ollama_url)

    cfg.auto_triage = prompt_bool_with_default(t("interactive.label_auto_triage"), cfg.auto_triage)

    cfg.auto_draft = prompt_bool_with_default(t("interactive.label_auto_draft"), cfg.auto_draft)
    if cfg.auto_draft:
        cfg.drafts_folder = prompt_with_default(t("interactive.label_drafts_folder"), cfg.drafts_folder)

    cfg.auto_contacts_lazy = prompt_bool_with_default(t("interactive.label_auto_contacts"), cfg.auto_contacts_lazy)
    if cfg.auto_contacts_lazy:
        cfg.sent_folder = prompt_with_default(t("interactive.label_sent_folder"), cfg.sent_folder)

    return cfg
