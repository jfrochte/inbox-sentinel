"""
main.py – Orchestrierung (der Klebstoff zwischen allen Modulen).

Abhaengigkeiten innerhalb des Pakets:
  - config (Config, Defaults, Debug-Flags)
  - interactive (alle Benutzer-Prompts, Profil-Auswahl)
  - utils (Dateihelfer, Logging, load_prompt_file)
  - imap_client (E-Mail-Abruf, Auto-Sort)
  - threading (Thread-Gruppierung per Union-Find)
  - llm (LLM-Analyse, Validierung, Repair)
  - report (Sortierung, HTML-Erzeugung, Block-Parsing)
  - smtp_client (Versand)
  - drafts (Auto-Draft: LLM-Antwortentwuerfe)
  - contacts (Auto-Contacts: Sender-Wissensbank)

Dieser Modul enthaelt die main()-Funktion, die den gesamten Ablauf steuert:
  1) Profil laden (optional)
  2) Alle Parameter interaktiv abfragen
  3) Profil speichern (optional)
  4) IMAP: Mails holen, in Threads gruppieren
  5) Pro Thread: Kontakt laden -> LLM-Analyse -> Auto-Draft -> Kontakt aktualisieren
  6) Sortieren, HTML-Report generieren
  7) Report per SMTP verschicken
  8) Drafts in IMAP speichern (optional)
  9) E-Mails in IMAP-Ordner sortieren (optional)
 10) Temp-Dateien loeschen (ausser Debug)
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
from email_report.config import Config, DEBUG_KEEP_FILES, DEBUG_LOG, REPORT_DIR, DEFAULT_SORT_FOLDERS
from email_report.interactive import (
    prompt_load_profile,
    prompt_save_profile,
    prompt_all_settings,
    prompt_confirm_or_edit,
    prompt_organization,
    prompt_user_settings,
    prompt_secret_with_default,
)
from email_report.utils import (
    log,
    load_prompt_file,
    safe_remove,
    append_secure,
    write_jsonl,
)
from email_report.imap_client import imap_fetch_emails_for_range, imap_move_emails
from email_report.llm import _analyze_thread_guaranteed
from email_report.threading import group_into_threads
from email_report.report import sort_summaries_by_priority, summaries_to_html, _parse_llm_summary_block
from email_report.smtp_client import send_email_html
from email_report.drafts import generate_draft_text, build_draft_message, imap_save_drafts
from email_report.contacts import (
    load_contact, save_contact, format_contact_for_prompt,
    merge_contact_update, extract_contact_info_via_llm,
)


# ============================================================
# Main
# ============================================================
def main():
    """Hauptablauf: siehe Modul-Docstring fuer Details."""
    # --- Profil laden ---
    cfg, profile_name = prompt_load_profile()

    edited = False
    if cfg is not None:
        # Flow A: Profil vorhanden -> Schnellstart
        cfg, edited = prompt_confirm_or_edit(cfg)
    else:
        # Flow B: Kein Profil -> Geführte Einrichtung
        edited = True
        cfg = Config()
        org = prompt_organization()
        if org is not None:
            # Org-Preset anwenden, dann nur User-Settings fragen
            cfg.organization = org["key"]
            cfg.imap_server = org["imap_server"]
            cfg.imap_port = org["imap_port"]
            cfg.smtp_server = org["smtp_server"]
            cfg.smtp_port = org["smtp_port"]
            cfg.smtp_ssl = org["smtp_ssl"]
            cfg = prompt_user_settings(cfg)
        else:
            # Eigener Server: voller Dialog wie bisher
            cfg = prompt_all_settings(cfg)

    # --- Prompt-Datei laden ---
    try:
        prompt_base = load_prompt_file(cfg.prompt_file)
    except FileNotFoundError:
        raise SystemExit(f"Prompt-Datei nicht gefunden: {cfg.prompt_file}")

    # --- Draft-Prompt laden (wenn auto_draft aktiv) ---
    draft_prompt_base = None
    if cfg.auto_draft:
        try:
            draft_prompt_base = load_prompt_file("draft_prompt.txt")
        except FileNotFoundError:
            log.warning("draft_prompt.txt nicht gefunden - Auto-Draft deaktiviert.")
            cfg.auto_draft = False

    # --- Contact-Prompt laden (wenn auto_contacts aktiv) ---
    contact_prompt_base = None
    if cfg.auto_contacts:
        try:
            contact_prompt_base = load_prompt_file("contact_prompt.txt")
        except FileNotFoundError:
            log.warning("contact_prompt.txt nicht gefunden - Auto-Contacts deaktiviert.")
            cfg.auto_contacts = False

    # --- Profil speichern (nur wenn etwas geaendert wurde) ---
    if edited:
        prompt_save_profile(cfg, default_name=profile_name)

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

    total_emails = len(emails)
    threads = group_into_threads(emails)
    log.info("Threading: %d E-Mails -> %d Threads", total_emails, len(threads))

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

    iterator = threads
    if tqdm is not None:
        iterator = tqdm(threads, desc="Verarbeite Threads")

    sort_moves = []  # Sammelt {"uid": ..., "folder": ...} fuer Auto-Sort
    draft_queue = []  # Sammelt (subject_log, Message) fuer IMAP APPEND
    draft_stats = {"generated": 0, "skipped": 0, "failed": 0}

    # --- Verarbeitung: pro Thread LLM Summary erzeugen ---
    for idx_thread, thread in enumerate(iterator, start=1):
        newest = thread[-1]
        thread_uids = [e.get("uid") for e in thread if e.get("uid")]

        # --- Punkt A: Kontakt laden (vor LLM-Analyse) ---
        sender_context = ""
        sender_addr = ""
        if cfg.auto_contacts:
            sender_addr = (newest.get("from_addr") or "").strip().lower()
            is_self = bool(cfg.from_email and cfg.from_email.lower() == sender_addr)
            if sender_addr and not is_self:
                existing_contact = load_contact(sender_addr)
                sender_context = format_contact_for_prompt(existing_contact)

        dbg = None
        if debug_log:
            dbg = {
                'idx': idx_thread,
                'thread_size': len(thread),
                'uids': thread_uids,
                'subject': newest.get('subject',''),
                'from': newest.get('from',''),
                'to': newest.get('to',''),
                'cc': newest.get('cc',''),
                'model': cfg.model,
                'ollama_url': cfg.ollama_url,
                'ts': datetime.now().isoformat(timespec='seconds'),
                'sender_context_len': len(sender_context),
                'sender_addr': sender_addr,
            }

        # --- Punkt B: sender_context an LLM-Analyse uebergeben ---
        final_block = _analyze_thread_guaranteed(cfg.model, thread, cfg.name, cfg.ollama_url, prompt_base, roles=cfg.roles, person_email=cfg.from_email, debug=dbg, sender_context=sender_context)

        if debug_log and debug_file and dbg is not None:
            st0 = (dbg.get('stage0') or {})
            keys = st0.get('json_keys') or []
            if dbg.get('final_status') == 'FALLBACK' and dbg.get('fallback_reason') == 'leere Antwort':
                if 'choices' in keys or 'message' in keys:
                    dbg['hint'] = 'Server liefert kein Feld "response" (sondern z.B. message/choices). Der Parser liest das jetzt; falls weiterhin leer: resp_text_head pruefen.'
            write_jsonl(debug_file, dbg)

        # parsed_block einmalig berechnen (wird von auto_sort UND auto_draft genutzt)
        parsed_block = _parse_llm_summary_block(final_block)

        # --- Auto-Sort ---
        if cfg.auto_sort and thread_uids:
            cat = (parsed_block.get("category") or "ACTIONABLE").strip().upper()
            if cat in DEFAULT_SORT_FOLDERS:
                for uid in thread_uids:
                    sort_moves.append({"uid": uid, "folder": DEFAULT_SORT_FOLDERS[cat]})

        # --- Auto-Draft ---
        if cfg.auto_draft and draft_prompt_base:
            cat = (parsed_block.get("category") or "ACTIONABLE").strip().upper()
            actions_raw = (parsed_block.get("actions") or "").strip()
            actions_low = actions_raw.lower()
            newest_from = (newest.get("from") or "").lower()
            is_self_sent = bool(cfg.from_email and cfg.from_email.lower() in newest_from)

            # Skip-Bedingungen: nicht-ACTIONABLE, self-sent, keine echten Actions
            if cat in ("SPAM", "PHISHING", "FYI"):
                draft_stats["skipped"] += 1
            elif is_self_sent:
                draft_stats["skipped"] += 1
            elif not actions_raw or actions_low in ("keine.", "keine", "none", "none.", "n/a"):
                draft_stats["skipped"] += 1
            else:
                try:
                    # --- Punkt C: sender_context an Draft uebergeben ---
                    draft_text = generate_draft_text(
                        cfg.model, thread, cfg.name, cfg.ollama_url,
                        draft_prompt_base, parsed_block, roles=cfg.roles,
                        sender_context=sender_context,
                    )
                    if draft_text:
                        draft_msg = build_draft_message(thread, draft_text, cfg.from_email, cfg.name)
                        subj_log = (parsed_block.get("subject") or newest.get("subject") or "?")[:80]
                        draft_queue.append((subj_log, draft_msg))
                        draft_stats["generated"] += 1
                        final_block += "\nDraft-Status: erstellt\n"
                    else:
                        draft_stats["failed"] += 1
                        log.warning("Draft-LLM lieferte leeren Text fuer: %s",
                                    (newest.get("subject") or "?")[:80])
                except Exception as e:
                    draft_stats["failed"] += 1
                    log.warning("Draft-Fehler fuer '%s': %s",
                                (newest.get("subject") or "?")[:80], e)

        # --- Punkt D: Kontakt per LLM aktualisieren ---
        if cfg.auto_contacts and sender_addr:
            try:
                existing_contact = load_contact(sender_addr)
                llm_extracted = extract_contact_info_via_llm(
                    cfg.model, thread, cfg.name, cfg.ollama_url,
                    prompt_base=contact_prompt_base,
                    existing_contact=existing_contact)
                if llm_extracted:
                    display_name = (newest.get("from") or "").strip()
                    email_date = (newest.get("date") or "")
                    updated = merge_contact_update(
                        existing_contact, llm_extracted, sender_addr, display_name, email_date)
                    save_contact(sender_addr, updated)
            except Exception as e:
                log.debug("Contact-Update fehlgeschlagen fuer %s: %s", sender_addr, e)

        append_secure(summaries_file, final_block)
        append_secure(summaries_file, "\n\n-----------------------\n\n")

    # --- Sortieren nach Priority ---
    sort_summaries_by_priority(summaries_file, sorted_file)

    # --- HTML Body bauen ---
    with open(sorted_file, "r", encoding="utf-8") as f:
        sorted_text = f.read()

    subject = f"Daily Email Report ({start_day.isoformat()} bis {end_day.isoformat()})"
    html_content = summaries_to_html(sorted_text, title=subject, expected_count=len(threads), auto_sort=cfg.auto_sort, total_emails=total_emails, draft_stats=draft_stats if cfg.auto_draft else None)

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
        # --- Auto-Draft: Drafts in IMAP speichern (VOR Auto-Sort) ---
        if sent_ok and cfg.auto_draft and draft_queue:
            log.info("Auto-Draft: %d Entwurf/Entwuerfe zum Speichern.", len(draft_queue))
            try:
                draft_result = imap_save_drafts(
                    username=cfg.username,
                    password=cfg.password,
                    imap_server=cfg.imap_server,
                    imap_port=cfg.imap_port,
                    drafts_folder=cfg.drafts_folder,
                    draft_messages=draft_queue,
                )
                log.info("Auto-Draft Ergebnis: %d gespeichert, %d fehlgeschlagen.",
                         draft_result["saved"], draft_result["failed"])
                if draft_result["errors"]:
                    for err in draft_result["errors"]:
                        log.warning("Auto-Draft Fehler: %s", err)
            except Exception as e:
                log.warning("Auto-Draft fehlgeschlagen: %s", e)

        # --- Auto-Sort: NACH dem Versand (Report hat Vorrang) ---
        if sent_ok and cfg.auto_sort and sort_moves:
            log.info("Auto-Sort: %d E-Mail(s) zum Verschieben.", len(sort_moves))
            try:
                sort_result = imap_move_emails(
                    username=cfg.username,
                    password=cfg.password,
                    imap_server=cfg.imap_server,
                    imap_port=cfg.imap_port,
                    mailbox=cfg.mailbox,
                    moves=sort_moves,
                )
                log.info("Auto-Sort Ergebnis: %d verschoben, %d fehlgeschlagen.",
                         sort_result["moved"], sort_result["failed"])
                if sort_result["errors"]:
                    for err in sort_result["errors"]:
                        log.warning("Auto-Sort Fehler: %s", err)
            except Exception as e:
                log.warning("Auto-Sort fehlgeschlagen: %s", e)

        # Nach erfolgreichem Versand: Dateien loeschen, ausser Debug
        if sent_ok and (not debug_keep):
            safe_remove(summaries_file)
            safe_remove(sorted_file)
