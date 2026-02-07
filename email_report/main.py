"""
main.py – Orchestrierung (der Klebstoff zwischen allen Modulen).

Abhaengigkeiten innerhalb des Pakets:
  - config (Config, Defaults, Debug-Flags)
  - interactive (alle Benutzer-Prompts, Profil-Auswahl)
  - utils (Dateihelfer, Logging, load_prompt_file)
  - imap_client (E-Mail-Abruf)
  - llm (LLM-Analyse)
  - report (Sortierung, HTML-Erzeugung)
  - smtp_client (Versand)

Dieser Modul enthaelt die main()-Funktion, die den gesamten Ablauf steuert:
  1) Profil laden (optional)
  2) Alle Parameter interaktiv abfragen
  3) Profil speichern (optional)
  4) IMAP: Mails holen
  5) LLM Summaries erzeugen
  6) Sortieren
  7) HTML Mail an sich selbst schicken
  8) Temp-Dateien loeschen (ausser Debug)
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import os
from datetime import datetime, timedelta

# tqdm ist optional: wenn installiert, gibt es Fortschrittsbalken.
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.config import Config, DEBUG_KEEP_FILES, DEBUG_LOG, REPORT_DIR
from email_report.interactive import (
    prompt_load_profile,
    prompt_save_profile,
    prompt_all_settings,
    prompt_secret_with_default,
)
from email_report.utils import (
    log,
    load_prompt_file,
    safe_remove,
    append_secure,
    write_jsonl,
)
from email_report.imap_client import imap_fetch_emails_for_range
from email_report.llm import _analyze_email_guaranteed
from email_report.report import sort_summaries_by_priority, summaries_to_html
from email_report.smtp_client import send_email_html


# ============================================================
# Main
# ============================================================
def main():
    """
    Ablauf:
    1) Profil laden (optional)
    2) Alle Parameter am Anfang abfragen (Return => Default / Profilwert)
    3) Profil speichern (optional)
    4) IMAP: Mails holen
    5) LLM Summaries
    6) Sortieren
    7) HTML Mail an sich selbst schicken
    8) Files loeschen (ausser Debug)
    """
    # --- Profil laden ---
    cfg = prompt_load_profile()
    if cfg is None:
        cfg = Config()

    # --- Alle Einstellungen interaktiv abfragen (Profilwerte als Defaults) ---
    cfg = prompt_all_settings(cfg)

    # --- Prompt-Datei laden ---
    try:
        prompt_base = load_prompt_file(cfg.prompt_file)
    except FileNotFoundError:
        raise SystemExit(f"Prompt-Datei nicht gefunden: {cfg.prompt_file}")

    # --- Profil speichern ---
    prompt_save_profile(cfg)

    print("\nKonfiguration (Achtung für Passwort KEIN Default):\n")

    # Passwort
    cfg.password = prompt_secret_with_default("Passwort")

    # Punkt 7: kleine Plausibilitaetswarnung
    # Viele Server erwarten, dass from_email in irgendeiner Form zur Auth passt.
    if "@" in cfg.username and cfg.from_email.lower() != cfg.username.lower():
        log.info("Hinweis: From E-Mail (%s) ist ungleich Username (%s). "
                 "Je nach SMTP-Policy kann das Probleme machen.", cfg.from_email, cfg.username)

    # --- IMAP: E-Mails holen ---
    # skip_own_sent wird jetzt explizit uebergeben statt als globales Flag
    emails = imap_fetch_emails_for_range(
        username=cfg.username,
        password=cfg.password,
        from_email=cfg.from_email,
        days_back=cfg.days_back,
        imap_server=cfg.imap_server,
        imap_port=cfg.imap_port,
        mailbox=cfg.mailbox,
        use_sentdate=cfg.use_sentdate,
        skip_own_sent=cfg.skip_own_sent,
    )
    if not emails:
        print("Keine E-Mails im gewaehlten Zeitraum gefunden.")
        return

    # --- Report-Dateien vorbereiten ---
    start_day = (datetime.now() - timedelta(days=cfg.days_back)).date()
    end_day = datetime.now().date()
    report_range = f"{start_day.isoformat()}_bis_{end_day.isoformat()}"

    report_dir = cfg.report_dir
    os.makedirs(report_dir, exist_ok=True)

    summaries_file = os.path.join(report_dir, f"zusammenfassung_{report_range}.txt")
    sorted_file = os.path.join(report_dir, f"zusammenfassung-sortiert_{report_range}.txt")

    debug_keep = cfg.debug_keep_files
    debug_log = cfg.debug_log
    debug_file = None
    if debug_log:
        debug_file = os.path.join(report_dir, f"debug_{report_range}.jsonl")
        print(f"Debug aktiv: schreibe {debug_file}")
        write_jsonl(debug_file, {"run_start": datetime.now().isoformat(timespec="seconds"), "ollama_url": cfg.ollama_url, "model": cfg.model})

    # Bei Nicht-Debug: vorhandene Dateien dieses Tages entfernen
    if not debug_keep:
        safe_remove(summaries_file)
        safe_remove(sorted_file)

    iterator = emails
    if tqdm is not None:
        iterator = tqdm(emails, desc="Verarbeite E-Mails")

    # --- Verarbeitung: pro Mail LLM Summary erzeugen ---
    for idx_mail, e in enumerate(iterator, start=1):
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

        dbg = None
        if debug_log:
            dbg = {
                'idx': idx_mail,
                'subject': e.get('subject',''),
                'from': e.get('from',''),
                'to': e.get('to',''),
                'cc': e.get('cc',''),
                'model': cfg.model,
                'ollama_url': cfg.ollama_url,
                'ts': datetime.now().isoformat(timespec='seconds'),
            }

        final_block = _analyze_email_guaranteed(cfg.model, e, cfg.name, cfg.ollama_url, prompt_base, debug=dbg)

        if debug_log and debug_file and dbg is not None:
            # Schneller Hinweis, wenn "response" fehlt und deshalb frueher alles leer war.
            st0 = (dbg.get('stage0') or {})
            keys = st0.get('json_keys') or []
            if dbg.get('final_status') == 'FALLBACK' and dbg.get('fallback_reason') == 'leere Antwort':
                if 'choices' in keys or 'message' in keys:
                    dbg['hint'] = 'Server liefert kein Feld "response" (sondern z.B. message/choices). Der Parser liest das jetzt; falls weiterhin leer: resp_text_head pruefen.'
            write_jsonl(debug_file, dbg)

        append_secure(summaries_file, final_block)
        append_secure(summaries_file, "\n\n-----------------------\n\n")

    # --- Sortieren nach Priority ---
    sort_summaries_by_priority(summaries_file, sorted_file)

    # --- HTML Body bauen ---
    with open(sorted_file, "r", encoding="utf-8") as f:
        sorted_text = f.read()

    subject = f"Daily Email Report ({start_day.isoformat()} bis {end_day.isoformat()})"
    html_content = summaries_to_html(sorted_text, title=subject, expected_count=len(emails))

    # --- Versenden ---
    sent_ok = False
    try:
        send_email_html(
            username=cfg.username,
            password=cfg.password,
            from_email=cfg.from_email,
            recipient_email=cfg.recipient_email,
            subject=subject,
            html_content=html_content,
            plain_text=sorted_text,
            smtp_server=cfg.smtp_server,
            smtp_port=cfg.smtp_port,
            smtp_ssl=cfg.smtp_ssl,
        )
        sent_ok = True
    finally:
        # Nach erfolgreichem Versand: Dateien loeschen, ausser Debug
        if sent_ok and (not debug_keep):
            safe_remove(summaries_file)
            safe_remove(sorted_file)
