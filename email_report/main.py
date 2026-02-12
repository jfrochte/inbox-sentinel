"""
main.py -- Orchestration (the glue between all modules).

Dependencies within the package:
  - config (Config, defaults, debug flags)
  - utils (file helpers, logging, load_prompt_file)
  - imap_client (email retrieval, auto-triage)
  - threading (thread grouping via union-find)
  - llm (LLM analysis, validation, repair)
  - report (sorting, HTML generation, block parsing)
  - smtp_client (sending)
  - drafts (auto-draft: LLM reply drafts)
  - contacts (auto-contacts: sender knowledge base)

run_pipeline() is the main service function (no CLI I/O).
"""

# ============================================================
# External dependencies
# ============================================================
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta

# ============================================================
# Internal package imports
# ============================================================
from email_report.config import Config, DEFAULT_SORT_FOLDERS
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
from email_report.llm_profiles import load_llm_profiles
from email_report.i18n import set_language


# ============================================================
# Contact-build service functions
# ============================================================
def build_single_contact(cfg: Config, addr: str, contact_prompt_base: str,
                         llm_profile: dict | None = None) -> dict:
    """Builds a single contact card via IMAP material collection + LLM.
    Returns {"success": bool, "emails_found": int, "message": str}."""
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
        return {"success": False, "emails_found": 0, "message": f"No emails found for {addr}"}
    existing = load_contact(addr)
    card = build_contact_card(
        cfg.model, addr, cfg.name, cfg.ollama_url,
        contact_prompt_base, collected, existing_contact=existing,
        llm_profile=llm_profile, language=cfg.language,
    )
    if card:
        save_contact(addr, card)
        return {"success": True, "emails_found": len(collected), "message": f"Card saved: {addr}"}
    return {"success": False, "emails_found": len(collected), "message": f"Card build failed for {addr}"}


def build_top_contacts(cfg: Config, contact_prompt_base: str,
                       llm_profile: dict | None = None,
                       progress_cb=None) -> dict:
    """Finds top-10 senders without a card and builds cards.
    Returns {"built": int, "total": int, "candidates": [(addr, count)]}."""
    emails = imap_fetch_emails_for_range(
        username=cfg.username, password=cfg.password,
        from_email=cfg.from_email, days_back=90,
        imap_server=cfg.imap_server, imap_port=cfg.imap_port,
        mailbox=cfg.mailbox, use_sentdate=cfg.use_sentdate,
        skip_own_sent=True,
    )
    if not emails:
        return {"built": 0, "total": 0, "candidates": []}

    freq = Counter(e.get("from_addr", "").strip().lower() for e in emails)
    freq.pop("", None)
    if cfg.from_email:
        freq.pop(cfg.from_email.lower(), None)

    candidates = []
    for addr, count in freq.most_common(30):
        if load_contact(addr) is None:
            candidates.append((addr, count))
        if len(candidates) >= 10:
            break

    if not candidates:
        return {"built": 0, "total": 0, "candidates": []}

    built = 0
    for i, (addr, count) in enumerate(candidates):
        if progress_cb:
            progress_cb("building_contacts", i + 1, len(candidates))
        result = build_single_contact(cfg, addr, contact_prompt_base, llm_profile=llm_profile)
        if result["success"]:
            built += 1

    return {"built": built, "total": len(candidates), "candidates": candidates}


# ============================================================
# Pipeline (main service function)
# ============================================================
def run_pipeline(cfg: Config, password: str, progress_cb=None,
                 start_date=None, end_date=None) -> dict:
    """Runs the full email analysis pipeline.

    Parameters:
        cfg: Fully configured Config (all fields except password set)
        password: IMAP/SMTP password (not stored in Config for security)
        progress_cb: Optional callback(phase: str, current: int, total: int)
        start_date: Optional date object for explicit range start
        end_date: Optional date object for explicit range end

    Returns dict:
        {
            "html": str,           # Generated HTML report
            "sorted_text": str,    # Plain-text sorted summaries
            "subject": str,        # Report subject line
            "draft_stats": dict,   # {"generated": N, "skipped": N, "failed": N}
            "triage_stats": dict,  # {"processed": N, "skipped": N, "failed": N}
            "total_emails": int,
            "thread_count": int,
        }
    """
    cfg.password = password

    # --- Initialize i18n and LLM profiles ---
    set_language(cfg.language)
    llm_profiles = load_llm_profiles()
    extraction_profile = llm_profiles["extraction"]
    creative_profile = llm_profiles["creative"]

    # --- Load prompt files ---
    prompt_base = load_prompt_file(cfg.prompt_file)

    draft_prompt_base = None
    if cfg.auto_draft:
        try:
            draft_prompt_base = load_prompt_file("draft_prompt.txt")
        except FileNotFoundError:
            log.warning("draft_prompt.txt not found - auto-draft disabled.")
            cfg.auto_draft = False

    contact_prompt_base = None
    if cfg.auto_contacts_lazy:
        try:
            contact_prompt_base = load_prompt_file("contact_prompt.txt")
        except FileNotFoundError:
            log.warning("contact_prompt.txt not found - auto-contacts disabled.")
            cfg.auto_contacts_lazy = False

    # --- IMAP: fetch emails ---
    if progress_cb:
        progress_cb("fetching", 0, 0)
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
        progress_cb=progress_cb,
        start_date=start_date,
        end_date=end_date,
    )
    if not emails:
        return {
            "html": "", "sorted_text": "", "subject": "",
            "draft_stats": {"generated": 0, "skipped": 0, "failed": 0},
            "triage_stats": {"processed": 0, "skipped": 0, "failed": 0},
            "total_emails": 0, "thread_count": 0,
        }

    total_emails = len(emails)
    threads = group_into_threads(emails)
    thread_count = len(threads)
    log.info("Threading: %d emails -> %d threads", total_emails, thread_count)

    # Report email/thread counts before analysis begins
    if progress_cb:
        progress_cb("analyzing", 0, thread_count, total_emails)

    # --- Prepare temp files for summaries ---
    start_day = (datetime.now() - timedelta(days=cfg.days_back)).date()
    end_day = datetime.now().date()
    report_range = f"{start_day.isoformat()}_bis_{end_day.isoformat()}"

    tmpdir = tempfile.mkdtemp(prefix="sentinel_")
    summaries_file = os.path.join(tmpdir, f"zusammenfassung_{report_range}.txt")
    sorted_file = os.path.join(tmpdir, f"zusammenfassung-sortiert_{report_range}.txt")

    debug_log = cfg.debug_log
    debug_file = None
    if debug_log:
        report_dir = cfg.report_dir
        os.makedirs(report_dir, exist_ok=True)
        debug_file = os.path.join(report_dir, f"debug_{report_range}.jsonl")
        write_jsonl(debug_file, {"run_start": datetime.now().isoformat(timespec="seconds"),
                                  "ollama_url": cfg.ollama_url, "model": cfg.model})

    sort_actions = []
    draft_queue = []
    draft_stats = {"generated": 0, "skipped": 0, "failed": 0}

    # ============================================================
    # Pass 1: LLM analysis per thread
    # All analysis calls share the same prompt.txt prefix, so
    # Ollama's KV cache stays warm across consecutive calls.
    # ============================================================
    analysis_results = []

    for idx_thread, thread in enumerate(threads, start=1):
        if progress_cb:
            progress_cb("analyzing", idx_thread, thread_count)

        newest = thread[-1]
        thread_uids = [e.get("uid") for e in thread if e.get("uid")]

        # --- Load contact ---
        sender_context = ""
        sender_addr = (newest.get("from_addr") or "").strip().lower()
        is_self = bool(cfg.from_email and cfg.from_email.lower() == sender_addr)
        if sender_addr and not is_self:
            existing_contact = load_contact(sender_addr)
            if existing_contact is None and cfg.auto_contacts_lazy and contact_prompt_base:
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
                            llm_profile=extraction_profile,
                            language=cfg.language,
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
                'subject': newest.get('subject', ''),
                'from': newest.get('from', ''),
                'to': newest.get('to', ''),
                'cc': newest.get('cc', ''),
                'model': cfg.model,
                'ollama_url': cfg.ollama_url,
                'ts': datetime.now().isoformat(timespec='seconds'),
                'sender_context_len': len(sender_context),
                'sender_addr': sender_addr,
            }

        final_block = _analyze_thread_guaranteed(
            cfg.model, thread, cfg.name, cfg.ollama_url, prompt_base,
            roles=cfg.roles, person_email=cfg.from_email,
            llm_profile=extraction_profile, debug=dbg,
            sender_context=sender_context,
        )

        if debug_log and debug_file and dbg is not None:
            st0 = (dbg.get('stage0') or {})
            keys = st0.get('json_keys') or []
            if dbg.get('final_status') == 'FALLBACK' and dbg.get('fallback_reason') == 'leere Antwort':
                if 'choices' in keys or 'message' in keys:
                    dbg['hint'] = ('Server returns no "response" field '
                                   '(e.g. message/choices instead). The parser '
                                   'handles this now; if still empty: check resp_text_head.')
            write_jsonl(debug_file, dbg)

        parsed_block = _parse_llm_summary_block(final_block)

        # --- Auto-triage preparation (no LLM, just bookkeeping) ---
        if cfg.auto_triage and thread_uids:
            cat = (parsed_block.get("category") or "ACTIONABLE").strip().upper()
            prio = 3
            try:
                prio = int(parsed_block.get("priority") or 3)
            except (ValueError, TypeError):
                pass

            if cat in DEFAULT_SORT_FOLDERS:
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": DEFAULT_SORT_FOLDERS[cat],
                                         "priority": prio, "extra_flags": ["\\Seen"]})
            elif cat == "FYI":
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": []})
            elif cat == "ACTIONABLE" and prio <= 2:
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": ["\\Flagged"]})
            elif cat == "ACTIONABLE":
                for uid in thread_uids:
                    sort_actions.append({"uid": uid, "folder": cfg.mailbox,
                                         "priority": prio, "extra_flags": []})

        analysis_results.append({
            "thread": thread,
            "final_block": final_block,
            "parsed_block": parsed_block,
            "sender_context": sender_context,
            "newest": newest,
        })

    # ============================================================
    # Pass 2: Draft generation (separate pass)
    # All draft calls share the same draft_prompt.txt prefix, so
    # Ollama's KV cache stays warm across consecutive calls.
    # ============================================================
    if cfg.auto_draft and draft_prompt_base:
        # First determine which threads are eligible for drafts
        draft_eligible = []
        for r in analysis_results:
            cat = (r["parsed_block"].get("category") or "ACTIONABLE").strip().upper()
            actions_raw = (r["parsed_block"].get("actions") or "").strip()
            actions_low = actions_raw.lower()
            newest_from = (r["newest"].get("from") or "").lower()
            is_self_sent = bool(cfg.from_email and cfg.from_email.lower() in newest_from)

            if cat in ("SPAM", "PHISHING", "FYI"):
                draft_stats["skipped"] += 1
            elif is_self_sent:
                draft_stats["skipped"] += 1
            elif not actions_raw or actions_low in ("keine.", "keine", "none", "none.", "n/a"):
                draft_stats["skipped"] += 1
            else:
                draft_eligible.append(r)

        # Generate drafts consecutively (KV cache warm for draft_prompt.txt)
        for idx_draft, r in enumerate(draft_eligible, start=1):
            if progress_cb:
                progress_cb("drafting", idx_draft, len(draft_eligible))
            try:
                draft_text = generate_draft_text(
                    cfg.model, r["thread"], cfg.name, cfg.ollama_url,
                    draft_prompt_base, r["parsed_block"], roles=cfg.roles,
                    sender_context=r["sender_context"],
                    llm_profile=creative_profile,
                )
                if draft_text:
                    draft_msg = build_draft_message(
                        r["thread"], draft_text, cfg.from_email, cfg.name,
                        signature_file=cfg.signature_file,
                    )
                    subj_log = (r["parsed_block"].get("subject") or r["newest"].get("subject") or "?")[:80]
                    draft_queue.append((subj_log, draft_msg))
                    draft_stats["generated"] += 1
                    r["final_block"] += "\nDraft-Status: ok\n"
                else:
                    draft_stats["failed"] += 1
                    log.warning("Draft LLM returned empty text for: %s",
                                (r["newest"].get("subject") or "?")[:80])
            except Exception as e:
                draft_stats["failed"] += 1
                log.warning("Draft error for '%s': %s",
                            (r["newest"].get("subject") or "?")[:80], e)

    # --- Compute stats from analysis results ---
    unique_senders = len(set(
        r["newest"].get("from_addr", "").strip().lower()
        for r in analysis_results
        if r["newest"].get("from_addr", "").strip()
    ))
    category_counts = dict(Counter(
        (r["parsed_block"].get("category") or "ACTIONABLE").strip().upper()
        for r in analysis_results
    ))

    # --- Write all blocks to summaries file ---
    for r in analysis_results:
        append_secure(summaries_file, r["final_block"])
        append_secure(summaries_file, f"\n\n{BLOCK_SEPARATOR}\n\n")

    # --- Sort + HTML ---
    if progress_cb:
        progress_cb("generating_report", 0, 0)

    sort_summaries_by_priority(summaries_file, sorted_file)

    with open(sorted_file, "r", encoding="utf-8") as f:
        sorted_text = f.read()

    subject = f"Daily Email Report ({start_day.isoformat()} bis {end_day.isoformat()})"
    html_content = summaries_to_html(
        sorted_text, title=subject, expected_count=thread_count,
        auto_triage=cfg.auto_triage, total_emails=total_emails,
        draft_stats=draft_stats if cfg.auto_draft else None,
    )

    # --- Send report ---
    if progress_cb:
        progress_cb("sending", 0, 0)

    sent_ok = False
    triage_stats = {"processed": 0, "skipped": 0, "failed": 0}
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
        # --- Save drafts (BEFORE auto-triage) ---
        if sent_ok and cfg.auto_draft and draft_queue:
            if progress_cb:
                progress_cb("saving_drafts", 0, 0)
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

        # --- Auto-triage ---
        if sent_ok and cfg.auto_triage and sort_actions:
            if progress_cb:
                progress_cb("triage", 0, 0)
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
                triage_stats = {
                    "processed": sort_result["processed"],
                    "skipped": sort_result["skipped"],
                    "failed": sort_result["failed"],
                }
                log.info("Auto-triage result: %d processed, %d skipped, %d failed.",
                         sort_result["processed"], sort_result["skipped"], sort_result["failed"])
                if not sort_result["keywords_supported"]:
                    log.warning("Auto-triage: server does not support keywords -- "
                                "idempotency not possible.")
                if sort_result["errors"]:
                    for err in sort_result["errors"]:
                        log.warning("Auto-triage error: %s", err)
            except Exception as e:
                log.warning("Auto-triage failed: %s", e)

        # Clean up temp files
        safe_remove(summaries_file)
        safe_remove(sorted_file)
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass

    return {
        "html": html_content,
        "sorted_text": sorted_text,
        "subject": subject,
        "draft_stats": draft_stats,
        "triage_stats": triage_stats,
        "total_emails": total_emails,
        "thread_count": thread_count,
        "unique_senders": unique_senders,
        "categories": category_counts,
    }
