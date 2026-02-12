"""
service.py -- Thin adapter layer over existing business-logic modules.
"""

import json
import os
import imaplib
import smtplib
import time

import requests

from email_report.config import Config, list_profiles, load_profile, save_profile, delete_profile
from email_report.contacts import load_contact, save_contact, build_contact_card, _email_to_filename, _CONTACTS_DIR
from email_report.imap_client import imap_fetch_for_contact
from email_report.vcard import read_vcard
from email_report.utils import log, load_prompt_file
from email_report.llm_profiles import load_llm_profiles
from email_report.i18n import set_language


# ============================================================
# Organizations
# ============================================================
_ORGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organizations.json")


def list_organizations() -> list[dict]:
    """Loads organization presets from organizations.json."""
    try:
        with open(_ORGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ============================================================
# LLM Models
# ============================================================
def fetch_llm_models(ollama_url: str) -> list[str]:
    """Fetches available model names from Ollama API."""
    # ollama_url is typically http://localhost:11434/api/generate
    # We need /api/tags
    base_url = ollama_url.rsplit("/api/", 1)[0] if "/api/" in ollama_url else ollama_url.rstrip("/")
    tags_url = f"{base_url}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models") or []
            return [m.get("name", "") for m in models if m.get("name")]
    except Exception:
        pass
    return []


# ============================================================
# Health Checks
# ============================================================
def check_imap(server: str, port: int, username: str, password: str) -> dict:
    """Tests IMAP connectivity. Returns {ok, message, latency_ms}."""
    t0 = time.time()
    try:
        mail = imaplib.IMAP4_SSL(server, port)
        mail.login(username, password)
        mail.logout()
        ms = int((time.time() - t0) * 1000)
        return {"ok": True, "message": "OK", "latency_ms": ms}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {"ok": False, "message": str(e), "latency_ms": ms}


def check_smtp(server: str, port: int, username: str, password: str, ssl: bool) -> dict:
    """Tests SMTP connectivity. Returns {ok, message, latency_ms}."""
    t0 = time.time()
    try:
        if ssl:
            srv = smtplib.SMTP_SSL(server, port, timeout=15)
        else:
            srv = smtplib.SMTP(server, port, timeout=15)
            srv.starttls()
        srv.login(username, password)
        srv.quit()
        ms = int((time.time() - t0) * 1000)
        return {"ok": True, "message": "OK", "latency_ms": ms}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {"ok": False, "message": str(e), "latency_ms": ms}


def check_llm(ollama_url: str) -> dict:
    """Tests LLM/Ollama reachability. Returns {ok, message, latency_ms}."""
    base_url = ollama_url.rsplit("/api/", 1)[0] if "/api/" in ollama_url else ollama_url.rstrip("/")
    tags_url = f"{base_url}/api/tags"
    t0 = time.time()
    try:
        resp = requests.get(tags_url, timeout=10)
        ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "message": "OK", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {"ok": False, "message": str(e), "latency_ms": ms}


# ============================================================
# Contact helpers
# ============================================================
def list_contacts() -> list[dict]:
    """Lists all contacts as summary dicts."""
    result = []
    if not os.path.isdir(_CONTACTS_DIR):
        return result
    for fname in sorted(os.listdir(_CONTACTS_DIR)):
        if not fname.endswith(".vcf"):
            continue
        path = os.path.join(_CONTACTS_DIR, fname)
        data = read_vcard(path)
        if data:
            email = (data.get("EMAIL") or fname[:-4].replace("_", "@"))
            result.append({
                "email": email,
                "fn": data.get("FN") or "",
                "org": data.get("ORG") or "",
                "title": data.get("TITLE") or "",
            })
    return result


def get_contact(email_addr: str) -> dict | None:
    """Loads a single contact."""
    return load_contact(email_addr)


def update_contact(email_addr: str, data: dict) -> None:
    """Saves/updates a contact."""
    save_contact(email_addr, data)


def delete_contact(email_addr: str) -> bool:
    """Deletes a contact file. Returns True on success."""
    path = os.path.join(_CONTACTS_DIR, _email_to_filename(email_addr))
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def preview_contact_update(email_addr: str, profile_name: str, password: str) -> dict | None:
    """Rebuilds a contact card via IMAP + LLM and returns the proposed data WITHOUT saving.
    Returns the proposed contact dict or None on failure."""
    cfg = load_profile(profile_name)
    cfg.password = password
    set_language(cfg.language)
    profiles = load_llm_profiles()
    prompt_base = load_prompt_file("contact_prompt.txt")

    folders = [cfg.mailbox]
    if cfg.sent_folder:
        folders.append(cfg.sent_folder)

    collected = imap_fetch_for_contact(
        username=cfg.username, password=cfg.password,
        imap_server=cfg.imap_server, imap_port=cfg.imap_port,
        contact_addr=email_addr, user_email=cfg.from_email,
        folders=folders,
    )
    if not collected:
        return None

    existing = load_contact(email_addr)
    card = build_contact_card(
        cfg.model, email_addr, cfg.name, cfg.ollama_url,
        prompt_base, collected, existing_contact=existing,
        llm_profile=profiles["extraction"], language=cfg.language,
    )
    return card
