"""
main.py -- Orchestration (the glue between all modules).

Dependencies within the package:
  - config (Config, defaults, debug flags)
  - interactive (all user prompts, profile selection)
  - utils (file helpers, logging, load_prompt_file)
  - imap_client (email retrieval, auto-triage)
  - threading (thread grouping via union-find)
  - llm (LLM analysis, validation, repair)
  - report (sorting, HTML generation, block parsing)
  - smtp_client (sending)
  - drafts (auto-draft: LLM reply drafts)
  - contacts (auto-contacts: sender knowledge base)

The main() function controls the entire workflow:
  1) Load profile (optional)
  2) Query all parameters interactively
  3) Save profile (optional)
  4) IMAP: fetch emails, group into threads
  5) Per thread: load contact -> LLM analysis -> auto-draft -> update contact
  6) Sort, generate HTML report
  7) Send report via SMTP
  8) Save drafts to IMAP (optional)
  9) Sort emails into IMAP folders (optional)
 10) Delete temp files (unless debug mode)
"""

# ============================================================
# External dependencies
# ============================================================
import argparse
import os
from collections import Counter
from datetime import datetime, timedelta

# tqdm is optional: if installed, a progress bar is displayed.
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# ============================================================
# Internal package imports
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
from email_report.imap_client import imap_fetch_emails_for_range, imap_fetch_for_contact, imap_safe_sort
from email_report.llm import _analyze_thread_guaranteed
from email_report.threading import group_into_threads
from email_report.report import sort_summaries_by_priority, summaries_to_html, _parse_llm_summary_block, BLOCK_SEPARATOR
from email_report.smtp_client import send_email_html
from email_report.drafts import generate_draft_text, build_draft_message, imap_save_drafts
from email_report.contacts import (
    load_contact, save_contact, format_contact_for_prompt,
    build_contact_card,
)


# ============================================================
# CLI: contact-build helpers
# ============================================================
def _build_single_contact(cfg, addr: str, contact_prompt_base: str) -> bool:
    """Builds a single contact card via IMAP material collection + LLM.
    Returns True on success."""
    folders = [cfg.mailbox]
    if cfg.sent_folder:
        folders.append(cfg.sent_folder)
    collected = imap_fetch_for_contact(
        username=cfg.username, password=cfg.password,
        imap_server=cfg.imap_server, imap_port=cfg.imap_port,
        contact_addr=addr, user_email=cfg.from_email,
        folders=folders,
    )
    if not collected:
        print(f"  No emails found for {addr}")
        return False
    print(f"  {len(collected)} email(s) collected for {addr}")
    existing = load_contact(addr)
    card = build_contact_card(
        cfg.model, addr, cfg.name, cfg.ollama_url,
        contact_prompt_base, collected, existing_contact=existing,
    )
    if card:
        save_contact(addr, card)
        print(f"  Card saved: {addr}")
        return True
    print(f"  Card build failed for {addr}")
    return False


def _build_top_contacts(cfg, contact_prompt_base: str) -> None:
    """Finds top-10 senders without a card and builds cards."""
    print("Collecting sender frequencies (last 90 days)...")
    emails = imap_fetch_emails_for_range(
        username=cfg.username, password=cfg.password,
        from_email=cfg.from_email, days_back=90,
        imap_server=cfg.imap_server, imap_port=cfg.imap_port,
        mailbox=cfg.mailbox, use_sentdate=cfg.use_sentdate,
        skip_own_sent=True,
    )
    if not emails:
        print("No emails found.")
        return

    freq = Counter(e.get("from_addr", "").strip().lower() for e in emails)
    freq.pop("", None)
    if cfg.from_email:
        freq.pop(cfg.from_email.lower(), None)

    # Top-10 without an existing card
    candidates = []
    for addr, count in freq.most_common(30):
        if load_contact(addr) is None:
            candidates.append((addr, count))
        if len(candidates) >= 10:
            break

    if not candidates:
        print("All top senders already have a card.")
        return

    print(f"{len(candidates)} sender(s) without a card found:")
    for addr, count in candidates:
        print(f"  {addr} ({count} emails)")

    built = 0
    for addr, count in candidates:
        print(f"\nBuilding card for {addr} ({count} emails)...")
        if _build_single_contact(cfg, addr, contact_prompt_base):
            built += 1

    print(f"\nDone: {built}/{len(candidates)} cards created.")


# ============================================================
# Main
# ============================================================
def main():
    """Main workflow: see module docstring for details."""
    # --- CLI arguments ---
    parser = argparse.ArgumentParser(
        description="Inbox Sentinel -- email report and contact management",
        add_help=False,
    )
    parser.add_argument("--build-contact", metavar="EMAIL",
                        help="Build a contact card for a single address")
    parser.add_argument("--build-contacts", action="store_true",
                        help="Find top-10 senders without a card and build cards")
    cli_args, _ = parser.parse_known_args()

    # --- Load profile ---
    cfg, profile_name = prompt_load_profile()

    # CLI mode: contact build
    if cli_args.build_contact or cli_args.build_contacts:
        if cfg is None:
            raise SystemExit("Please create a profile first.")
        cfg.password = prompt_secret_with_default("Passwort")
        try:
            contact_prompt_base = load_prompt_file("contact_prompt.txt")
        except FileNotFoundError:
            raise SystemExit("contact_prompt.txt not found.")
        if cli_args.build_contact:
            _build_single_contact(cfg, cli_args.build_contact, contact_prompt_base)
        else:
            _build_top_contacts(cfg, contact_prompt_base)
        return

    edited = False
    if cfg is not None:
        # Flow A: profile exists -> quick start
        cfg, edited = prompt_confirm_or_edit(cfg)
    else:
        # Flow B: no profile -> guided setup
        edited = True
        cfg = Config()
        org = prompt_organization()
        if org is not None:
            # Apply org preset, then only ask for user settings
            cfg.organization = org["key"]
            cfg.imap_server = org["imap_server"]
            cfg.imap_port = org["imap_port"]
            cfg.smtp_server = org["smtp_server"]
            cfg.smtp_port = org["smtp_port"]
            cfg.smtp_ssl = org["smtp_ssl"]
            cfg = prompt_user_settings(cfg)
        else:
            # Custom server: full dialog as before
            cfg = prompt_all_settings(cfg)

    # --- Load prompt file ---
    try:
        prompt_base = load_prompt_file(cfg.prompt_file)
    except FileNotFoundError:
        raise SystemExit(f"Prompt file not found: {cfg.prompt_file}")

    # --- Load draft prompt (if auto_draft is enabled) ---
    draft_prompt_base = None
    if cfg.auto_draft:
        try:
            draft_prompt_base = load_prompt_file("draft_prompt.txt")
        except FileNotFoundError:
            log.warning("draft_prompt.txt not found - auto-draft disabled.")
            cfg.auto_draft = False

    # --- Load contact prompt (if auto_contacts_lazy is enabled) ---
    contact_prompt_base = None
    if cfg.auto_contacts_lazy:
        try:
            contact_prompt_base = load_prompt_file("contact_prompt.txt")
        except FileNotFoundError:
            log.warning("contact_prompt.txt not found - auto-contacts disabled.")
            cfg.auto_contacts_lazy = False

    # --- Save profile (only if something changed) ---
    if edited:
        prompt_save_profile(cfg, default_name=profile_name)

    print("\nConfiguration (note: no default for password):\n")

    # Password
    cfg.password = prompt_secret_with_default("Passwort")

    # Many servers require from_email to match the authenticated username.
    if "@" in cfg.username and cfg.from_email.lower() != cfg.username.lower():
        log.info("Note: from_email (%s) differs from username (%s). "
                 "Depending on SMTP policy this may cause issues.", cfg.from_email, cfg.username)

    # --- IMAP: fetch emails ---
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
        print("No emails found in the selected period.")
        return

    total_emails = len(emails)
    threads = group_into_threads(emails)
    log.info("Threading: %d emails -> %d threads", total_emails, len(threads))

    # --- Prepare report files ---
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
        print(f"Debug active: writing {debug_file}")
        write_jsonl(debug_file, {"run_start": datetime.now().isoformat(timespec="seconds"), "ollama_url": cfg.ollama_url, "model": cfg.model})

    # Non-debug mode: remove existing files for this day
    if not debug_keep:
        safe_remove(summaries_file)
        safe_remove(sorted_file)

    iterator = threads
    if tqdm is not None:
        iterator = tqdm(threads, desc="Processing threads")

    sort_actions = []  # Collects {"uid", "folder", "priority", "extra_flags"} for auto-triage
    draft_queue = []  # Collects (subject_log, Message) for IMAP APPEND
    draft_stats = {"generated": 0, "skipped": 0, "failed": 0}

    # --- Processing: generate LLM summary per thread ---
    for idx_thread, thread in enumerate(iterator, start=1):
        newest = thread[-1]
        thread_uids = [e.get("uid") for e in thread if e.get("uid")]

        # --- Load contact (before LLM analysis, whenever a card exists) ---
        sender_context = ""
        sender_addr = (newest.get("from_addr") or "").strip().lower()
        is_self = bool(cfg.from_email and cfg.from_email.lower() == sender_addr)
        if sender_addr and not is_self:
            existing_contact = load_contact(sender_addr)
            if existing_contact is None and cfg.auto_contacts_lazy and contact_prompt_base:
                # Lazy build: card does not exist yet, collect material and build
                try:
                    folders = [cfg.mailbox]
                    if cfg.sent_folder:
                        folders.append(cfg.sent_folder)
                    collected = imap_fetch_for_contact(
                        username=cfg.username, password=cfg.password,
                        imap_server=cfg.imap_server, imap_port=cfg.imap_port,
                        contact_addr=sender_addr, user_email=cfg.from_email,
                        folders=folders,
                    )
                    if collected:
                        card = build_contact_card(
                            cfg.model, sender_addr, cfg.name, cfg.ollama_url,
                            contact_prompt_base, collected,
                        )
                        if card:
                            save_contact(sender_addr, card)
                            existing_contact = card
                            log.info("Lazy build: contact card created for %s", sender_addr)
                except Exception as e:
                    log.debug("Lazy build failed for %s: %s", sender_addr, e)
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

        # --- Pass sender_context to LLM analysis ---
        final_block = _analyze_thread_guaranteed(cfg.model, thread, cfg.name, cfg.ollama_url, prompt_base, roles=cfg.roles, person_email=cfg.from_email, debug=dbg, sender_context=sender_context)

        if debug_log and debug_file and dbg is not None:
            st0 = (dbg.get('stage0') or {})
            keys = st0.get('json_keys') or []
            if dbg.get('final_status') == 'FALLBACK' and dbg.get('fallback_reason') == 'leere Antwort':
                if 'choices' in keys or 'message' in keys:
                    dbg['hint'] = 'Server returns no "response" field (e.g. message/choices instead). The parser handles this now; if still empty: check resp_text_head.'
            write_jsonl(debug_file, dbg)

        # Compute parsed_block once (used by both auto_triage and auto_draft)
        parsed_block = _parse_llm_summary_block(final_block)

        # --- Auto-triage: unified actions list ---
        if cfg.auto_triage and thread_uids:
            cat = (parsed_block.get("category") or "ACTIONABLE").strip().upper()
            prio = 3
            try:
                prio = int(parsed_block.get("priority") or 3)
            except (ValueError, TypeError):
                pass

            if cat in DEFAULT_SORT_FOLDERS:
                # SPAM/PHISHING -> move to quarantine folder (with X-Priority + \Seen)
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": DEFAULT_SORT_FOLDERS[cat],
                                         "priority": prio, "extra_flags": ["\\Seen"]})
            elif cat == "FYI":
                # FYI -> replace in INBOX (X-Priority only, no flag change)
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": []})
            elif cat == "ACTIONABLE" and prio <= 2:
                # High priority -> replace in INBOX (X-Priority + \Flagged)
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": ["\\Flagged"]})
            elif cat == "ACTIONABLE":
                # Normal/low priority -> replace in INBOX (X-Priority only)
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": []})

        # --- Auto-Draft ---
        if cfg.auto_draft and draft_prompt_base:
            cat = (parsed_block.get("category") or "ACTIONABLE").strip().upper()
            actions_raw = (parsed_block.get("actions") or "").strip()
            actions_low = actions_raw.lower()
            newest_from = (newest.get("from") or "").lower()
            is_self_sent = bool(cfg.from_email and cfg.from_email.lower() in newest_from)

            # Skip conditions: non-ACTIONABLE, self-sent, no real actions
            if cat in ("SPAM", "PHISHING", "FYI"):
                draft_stats["skipped"] += 1
            elif is_self_sent:
                draft_stats["skipped"] += 1
            elif not actions_raw or actions_low in ("keine.", "keine", "none", "none.", "n/a"):
                draft_stats["skipped"] += 1
            else:
                try:
                    # --- Pass sender_context to draft generation ---
                    draft_text = generate_draft_text(
                        cfg.model, thread, cfg.name, cfg.ollama_url,
                        draft_prompt_base, parsed_block, roles=cfg.roles,
                        sender_context=sender_context,
                    )
                    if draft_text:
                        draft_msg = build_draft_message(thread, draft_text, cfg.from_email, cfg.name,
                                                        signature_file=cfg.signature_file)
                        subj_log = (parsed_block.get("subject") or newest.get("subject") or "?")[:80]
                        draft_queue.append((subj_log, draft_msg))
                        draft_stats["generated"] += 1
                        final_block += "\nDraft-Status: erstellt\n"
                    else:
                        draft_stats["failed"] += 1
                        log.warning("Draft LLM returned empty text for: %s",
                                    (newest.get("subject") or "?")[:80])
                except Exception as e:
                    draft_stats["failed"] += 1
                    log.warning("Draft error for '%s': %s",
                                (newest.get("subject") or "?")[:80], e)

        append_secure(summaries_file, final_block)
        append_secure(summaries_file, f"\n\n{BLOCK_SEPARATOR}\n\n")

    # --- Sort by priority ---
    sort_summaries_by_priority(summaries_file, sorted_file)

    # --- Build HTML body ---
    with open(sorted_file, "r", encoding="utf-8") as f:
        sorted_text = f.read()

    subject = f"Daily Email Report ({start_day.isoformat()} bis {end_day.isoformat()})"
    html_content = summaries_to_html(sorted_text, title=subject, expected_count=len(threads), auto_triage=cfg.auto_triage, total_emails=total_emails, draft_stats=draft_stats if cfg.auto_draft else None)

    # --- Send report ---
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
        # --- Auto-draft: save drafts to IMAP (BEFORE auto-triage) ---
        if sent_ok and cfg.auto_draft and draft_queue:
            log.info("Auto-draft: %d draft(s) to save.", len(draft_queue))
            try:
                draft_result = imap_save_drafts(
                    username=cfg.username,
                    password=cfg.password,
                    imap_server=cfg.imap_server,
                    imap_port=cfg.imap_port,
                    drafts_folder=cfg.drafts_folder,
                    draft_messages=draft_queue,
                )
                log.info("Auto-draft result: %d saved, %d failed.",
                         draft_result["saved"], draft_result["failed"])
                if draft_result["errors"]:
                    for err in draft_result["errors"]:
                        log.warning("Auto-draft error: %s", err)
            except Exception as e:
                log.warning("Auto-draft failed: %s", e)

        # --- Auto-triage: AFTER sending (report has priority) ---
        if sent_ok and cfg.auto_triage and sort_actions:
            log.info("Auto-triage: %d email(s) to process.", len(sort_actions))
            try:
                sort_result = imap_safe_sort(
                    username=cfg.username,
                    password=cfg.password,
                    imap_server=cfg.imap_server,
                    imap_port=cfg.imap_port,
                    mailbox=cfg.mailbox,
                    sort_actions=sort_actions,
                )
                log.info("Auto-triage result: %d processed, "
                         "%d skipped, %d failed.",
                         sort_result["processed"],
                         sort_result["skipped"], sort_result["failed"])
                if not sort_result["keywords_supported"]:
                    log.warning("Auto-triage: server does not support keywords -- "
                                "idempotency not possible.")
                if sort_result["errors"]:
                    for err in sort_result["errors"]:
                        log.warning("Auto-triage error: %s", err)
            except Exception as e:
                log.warning("Auto-triage failed: %s", e)

        # After successful send: delete files unless debug mode
        if sent_ok and (not debug_keep):
            safe_remove(summaries_file)
            safe_remove(sorted_file)
