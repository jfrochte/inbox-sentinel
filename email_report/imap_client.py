"""
imap_client.py -- IMAP connection and email retrieval.

Dependencies within the package:
  - utils (logging)
  - email_parser (header decoding, body extraction)

UID-based fetch: UIDs are used instead of sequence numbers because
they remain stable across sessions (required for auto-triage).
"""

# ============================================================
# External dependencies
# ============================================================
import imaplib
import email
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime


# ============================================================
# Internal package imports
# ============================================================
from email_report.utils import log
from email_report.email_parser import (
    decode_mime_words,
    get_email_address_from_header,
    extract_best_body_text,
    extract_raw_body_text,
)


# ============================================================
# IMAP fetch (UID-based, optional SENT* search)
# ============================================================
def imap_fetch_emails_for_range(username: str, password: str, from_email: str, days_back: int,
                                imap_server: str, imap_port: int, mailbox: str,
                                use_sentdate: bool, skip_own_sent: bool = True,
                                progress_cb=None,
                                start_date=None, end_date=None):
    """
    Returns a list of dicts:
    {
      'uid': ...,
      'subject': ...,
      'from': ...,
      'from_addr': ...,
      'to': ...,
      'cc': ...,
      'body': ...,
    }

    The time range is defined as [today - days_back, tomorrow), i.e. inclusive of today and the last days_back days.

    Search logic:
    - use_sentdate True: (SENTSINCE <start> SENTBEFORE <end_excl>)
      based on the "Date:" header (send date)
    - use_sentdate False: (SINCE <start> BEFORE <end_excl>)
      based on INTERNALDATE (storage time)

    skip_own_sent: If True, own sent mails are skipped.
    """
    # Time range:
    # If explicit start_date/end_date are given, use them.
    # Otherwise: days_back = 0  -> today only
    #            days_back = 2  -> today + the last 2 days (3 calendar days total)
    if start_date and end_date:
        start_day = start_date
        end_day_excl = end_date + timedelta(days=1)   # end_date is inclusive, IMAP BEFORE is exclusive
    else:
        start_day = (datetime.now() - timedelta(days=days_back)).date()      # inclusive
        end_day_excl = datetime.now().date() + timedelta(days=1)            # exclusive (tomorrow)

    since_str = start_day.strftime("%d-%b-%Y")
    before_str = end_day_excl.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    emails = []

    try:
        mail.login(username, password)

        # read-only to prevent setting \Seen flag
        try:
            mail.select(mailbox, readonly=True)
        except Exception:
            mail.select(mailbox)

        if use_sentdate:
            # SENTSINCE/SENTBEFORE are standardized but not every server is 100% compatible
            query = f"(NOT DELETED SENTSINCE {since_str} SENTBEFORE {before_str})"
        else:
            query = f"(NOT DELETED SINCE {since_str} BEFORE {before_str})"

        status, data = mail.uid('search', None, query)
        if status != "OK":
            log.warning("IMAP UID search failed. status=%s query=%s", status, query)
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        total = len(msg_ids)
        for i, uid_bytes in enumerate(msg_ids):
            if progress_cb:
                progress_cb("fetching", i + 1, total)
            typ, msg_data = mail.uid('fetch', uid_bytes, "(BODY.PEEK[])")
            if typ != "OK":
                continue

            raw_bytes = None
            for part in msg_data:
                if isinstance(part, tuple):
                    raw_bytes = part[1]
                    break
            if not raw_bytes:
                continue

            message = email.message_from_bytes(raw_bytes)

            from_header = decode_mime_words(message.get("from"))
            from_addr = get_email_address_from_header(from_header).lower()

            if skip_own_sent and from_addr and from_addr == from_email.lower():
                continue

            subject = decode_mime_words(message.get("subject"))
            to_header = decode_mime_words(message.get("to"))
            cc_header = decode_mime_words(message.get("cc"))

            # Extract threading headers
            message_id = message.get("message-id") or ""
            in_reply_to = message.get("in-reply-to") or ""
            references_raw = message.get("references") or ""
            references = references_raw.split() if references_raw.strip() else []

            date_iso = ""
            date_raw = message.get("date")
            if date_raw:
                try:
                    date_iso = parsedate_to_datetime(date_raw).isoformat()
                except Exception:
                    pass

            body = extract_best_body_text(message)
            body_original = extract_raw_body_text(message)

            emails.append({
                "uid": uid_bytes.decode(),
                "subject": subject,
                "from": from_header,
                "from_addr": from_addr,
                "to": to_header,
                "cc": cc_header,
                "message_id": message_id.strip(),
                "in_reply_to": in_reply_to.strip(),
                "references": references,
                "date": date_iso,
                "body": body,
                "body_original": body_original,
            })

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return emails


# ============================================================
# IMAP fetch for contact material collection
# ============================================================
def imap_fetch_for_contact(
    username: str, password: str, imap_server: str, imap_port: int,
    contact_addr: str, user_email: str,
    folders: list[str],
    max_chars: int = 6000,
    max_days: int = 360,
) -> list[dict]:
    """
    Collects emails between contact_addr and user_email from multiple folders.

    For each mail the direction is determined:
      - 'incoming': contact_addr is the sender
      - 'outgoing': contact_addr appears in To/Cc

    Sorting: newest first. Text budget: collection stops once the cumulative
    body text reaches >= max_chars.

    Returns a list of dicts (same structure as imap_fetch_emails_for_range
    plus 'direction').
    """
    contact_low = contact_addr.strip().lower()
    user_low = user_email.strip().lower()

    since_date = (datetime.now() - timedelta(days=max_days)).date()
    since_str = since_date.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    raw_results = []

    try:
        mail.login(username, password)

        for folder in folders:
            if not folder or not folder.strip():
                continue
            try:
                status, _ = mail.select(folder.strip(), readonly=True)
                if status != "OK":
                    log.debug("Folder '%s' not selectable, skipped.", folder)
                    continue
            except Exception:
                log.debug("Folder '%s' not found, skipped.", folder)
                continue

            # Two separate searches (OR is not correctly supported by some servers)
            seen_uids = set()
            msg_ids = []
            for field in ("FROM", "TO"):
                query = f'(NOT DELETED SINCE {since_str} {field} "{contact_low}")'
                try:
                    status, data = mail.uid('search', None, query)
                except Exception:
                    log.debug("IMAP search %s in '%s' failed.", field, folder)
                    continue
                if status != "OK" or not data or not data[0]:
                    continue
                for uid_b in data[0].split():
                    if uid_b not in seen_uids:
                        seen_uids.add(uid_b)
                        msg_ids.append(uid_b)
            for uid_bytes in msg_ids:
                typ, msg_data = mail.uid('fetch', uid_bytes, "(BODY.PEEK[])")
                if typ != "OK":
                    continue

                raw_bytes = None
                for part in msg_data:
                    if isinstance(part, tuple):
                        raw_bytes = part[1]
                        break
                if not raw_bytes:
                    continue

                message = email.message_from_bytes(raw_bytes)

                from_header = decode_mime_words(message.get("from"))
                from_addr = get_email_address_from_header(from_header).lower()
                to_header = decode_mime_words(message.get("to"))
                cc_header = decode_mime_words(message.get("cc"))

                # Determine direction: based on From address only
                # (no cross-check on To/Cc because mailbox address and
                #  from_email can differ, e.g. with forwarding)
                direction = None
                if from_addr == contact_low:
                    direction = "incoming"
                elif contact_low in ((to_header or "") + " " + (cc_header or "")).lower():
                    direction = "outgoing"

                if not direction:
                    continue

                subject = decode_mime_words(message.get("subject"))

                date_iso = ""
                date_raw = message.get("date")
                if date_raw:
                    try:
                        date_iso = parsedate_to_datetime(date_raw).isoformat()
                    except Exception:
                        pass

                body = extract_best_body_text(message)

                raw_results.append({
                    "uid": uid_bytes.decode(),
                    "subject": subject,
                    "from": from_header,
                    "from_addr": from_addr,
                    "to": to_header,
                    "cc": cc_header,
                    "date": date_iso,
                    "body": body,
                    "direction": direction,
                })

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    # Sort: newest first
    raw_results.sort(key=lambda e: e.get("date", ""), reverse=True)

    # Apply text budget
    result = []
    total_chars = 0
    for entry in raw_results:
        result.append(entry)
        total_chars += len(entry.get("body") or "")
        if total_chars >= max_chars:
            break

    return result


# ============================================================
# IMAP auto-triage: crash-safe post-processing
# ============================================================
def _check_keyword_support(mail) -> bool:
    """Checks whether the mailbox supports custom keywords (flags).

    PERMANENTFLAGS containing '\\*' means the server allows arbitrary keywords.

    imaplib stores PERMANENTFLAGS as part of the OK responses (not as a
    separate key), so we need to search through mail.response("OK").
    """
    try:
        # PERMANENTFLAGS arrives as an untagged OK response from SELECT:
        # * OK [PERMANENTFLAGS (\Answered \Flagged \Deleted \Seen \Draft \*)]
        resp = mail.response("OK")
        if resp and resp[1]:
            for item in resp[1]:
                if item is None:
                    continue
                text = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
                if "PERMANENTFLAGS" in text and "\\*" in text:
                    return True
    except Exception:
        pass
    return False



def _inject_x_priority(raw_bytes: bytes, priority: int) -> bytes:
    """Sets the X-Priority header in a raw IMAP message.

    Replaces an existing X-Priority header or inserts a new one
    before the end of the header block (before \\r\\n\\r\\n).
    """
    # Find end of headers
    header_end = raw_bytes.find(b"\r\n\r\n")
    sep = b"\r\n"
    if header_end < 0:
        header_end = raw_bytes.find(b"\n\n")
        sep = b"\n"
    if header_end < 0:
        return raw_bytes  # No header/body separator found

    header_section = raw_bytes[:header_end]
    body_section = raw_bytes[header_end:]

    # Replace existing X-Priority header
    new_header, count = re.subn(
        rb"(?mi)^X-Priority:.*$",
        f"X-Priority: {priority}".encode(),
        header_section,
    )
    if count > 0:
        return new_header + body_section

    # No existing header found -- insert a new one
    x_prio = sep + f"X-Priority: {priority}".encode()
    return header_section + x_prio + body_section


def _fetch_message_data(mail, uid_bytes):
    """Fetches the raw message, INTERNALDATE, and FLAGS via UID FETCH.

    Returns: (raw_bytes, internaldate_str, flags_list) or (None, None, None).
    """
    try:
        status, data = mail.uid('fetch', uid_bytes, '(BODY.PEEK[] INTERNALDATE FLAGS)')
        if status != "OK" or not data:
            return None, None, None

        raw_bytes = None
        meta_str = ""
        for part in data:
            if isinstance(part, tuple):
                meta = part[0]
                raw_bytes = part[1]
                if isinstance(meta, bytes):
                    meta_str = meta.decode("utf-8", errors="replace")
                else:
                    meta_str = str(meta)
                break

        if not raw_bytes:
            return None, None, None

        # Extract INTERNALDATE from metadata
        # Important: imaplib.Time2Internaldate() expects the full IMAP format
        # 'INTERNALDATE "DD-Mon-YYYY..."' (not just the date value).
        internaldate = None
        m = re.search(r'INTERNALDATE "[^"]+"', meta_str)
        if m:
            internaldate = m.group(0)

        # Extract and filter FLAGS
        original_flags = []
        m_flags = re.search(r'FLAGS \(([^)]*)\)', meta_str)
        if m_flags:
            for flag in m_flags.group(1).split():
                # \Recent cannot be set, \Deleted should not be carried over
                if flag not in ("\\Recent", "\\Deleted"):
                    original_flags.append(flag)

        return raw_bytes, internaldate, original_flags
    except Exception as e:
        log.debug("FETCH failed for UID %s: %s", uid_bytes, e)
        return None, None, None


def imap_safe_sort(username: str, password: str, imap_server: str, imap_port: int,
                   mailbox: str, sort_actions: list[dict]) -> dict:
    """
    Crash-safe IMAP post-processing in a single session.

    Each action follows the same flow:
      FETCH -> inject X-Priority -> APPEND (with original flags + extra_flags)
      -> verify -> \\Deleted on original -> batch UID EXPUNGE (only with UIDPLUS).

    sort_actions: [{"uid": str, "folder": str, "priority": int,
                    "extra_flags": [str]}]
      - folder: target folder (INBOX for in-place replacement, Spam/Quarantine for move)
      - priority: X-Priority value (1-5) for the header
      - extra_flags: additional flags (e.g. ["\\\\Seen"], ["\\\\Flagged"])

    Safety guarantees:
    - No email loss: original is only deleted after copy is verified
    - Crash-safe: abort -> worst case is a duplicate
    - Idempotent: $Sentinel_Sorted prevents double-processing
    - Server-compatible: without UIDPLUS only \\Deleted (no EXPUNGE)

    Returns {"processed": int, "skipped": int, "failed": int,
             "errors": [str], "keywords_supported": bool, "has_uidplus": bool}.
    """
    from email_report.config import SENTINEL_KEYWORD

    result = {"processed": 0, "skipped": 0, "failed": 0,
              "errors": [], "keywords_supported": False, "has_uidplus": False}

    if not sort_actions:
        return result

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(username, password)

        # Check UIDPLUS capability (required for safe UID EXPUNGE)
        has_uidplus = False
        try:
            caps = mail.capabilities or ()
            has_uidplus = b"UIDPLUS" in caps or "UIDPLUS" in caps
        except Exception:
            pass
        result["has_uidplus"] = has_uidplus

        # Open source mailbox in read-write mode
        status, _ = mail.select(mailbox, readonly=False)
        if status != "OK":
            result["errors"].append(f"Could not open mailbox '{mailbox}'")
            return result

        # Check PERMANENTFLAGS: keywords supported?
        kw_supported = _check_keyword_support(mail)
        result["keywords_supported"] = kw_supported

        # --- Phase 1: Create target folders (non-INBOX only) ---
        created_folders = set()
        for action in sort_actions:
            folder_name = action.get("folder", "")
            if not folder_name or folder_name == mailbox or folder_name in created_folders:
                continue
            try:
                status_c, _ = mail.create(folder_name)
                if status_c == "OK":
                    log.info("IMAP folder created: %s", folder_name)
            except Exception:
                pass  # Folder probably already exists
            try:
                mail.subscribe(folder_name)
            except Exception:
                pass
            created_folders.add(folder_name)

        # --- Phase 2: Process all actions ---
        # FETCH -> X-Priority -> APPEND -> verify -> \Deleted on original
        deleted_uids = []  # For batch EXPUNGE at the end

        for action in sort_actions:
            uid = action.get("uid", "")
            folder_name = action.get("folder", "")
            priority = action.get("priority", 3)
            extra_flags = action.get("extra_flags", [])
            if not uid or not folder_name:
                continue

            uid_b = uid.encode() if isinstance(uid, str) else uid

            try:
                # FETCH raw message + INTERNALDATE + FLAGS
                raw_bytes, internaldate, orig_flags = _fetch_message_data(mail, uid_b)
                if not raw_bytes:
                    result["failed"] += 1
                    result["errors"].append(f"UID {uid}: FETCH failed")
                    continue

                # Set/replace X-Priority header
                modified = _inject_x_priority(raw_bytes, priority)

                # Build combined flags: original + extra + $Sentinel_Sorted
                combined_flags = set(orig_flags)
                for ef in extra_flags:
                    combined_flags.add(ef)
                if kw_supported:
                    combined_flags.add(SENTINEL_KEYWORD)
                # Do not carry \Deleted over to the new copy
                combined_flags.discard("\\Deleted")
                combined_flags.discard("\\Recent")
                flags_str = "(" + " ".join(sorted(combined_flags)) + ")"

                # APPEND to target folder (with combined flags + original date)
                try:
                    status_a, _ = mail.append(
                        folder_name,
                        flags_str,
                        internaldate,
                        modified,
                    )
                except ValueError:
                    # Fallback: INTERNALDATE could not be parsed
                    # -> append without date (server uses current timestamp)
                    log.debug("INTERNALDATE parse failed for UID %s, "
                              "falling back to server date", uid)
                    status_a, _ = mail.append(
                        folder_name,
                        flags_str,
                        None,
                        modified,
                    )
                if status_a != "OK":
                    result["failed"] += 1
                    result["errors"].append(f"UID {uid}: APPEND to {folder_name} failed")
                    continue

                # --- Copy verified, now clean up the original ---
                # Mark original as \Deleted
                try:
                    mail.uid('store', uid_b, '+FLAGS', '(\\Deleted)')
                    deleted_uids.append(uid)
                except Exception:
                    pass

                result["processed"] += 1

            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"UID {uid}: {e}")
                log.warning("Sort error for UID %s to %s: %s", uid, folder_name, e)

        # --- Phase 3: Batch EXPUNGE (only with UIDPLUS) ---
        if deleted_uids and has_uidplus:
            uid_set = ",".join(deleted_uids)
            try:
                mail.uid('expunge', uid_set)
            except Exception as e:
                log.warning("UID EXPUNGE failed (originals remain marked as "
                            "\\Deleted): %s", e)
        elif deleted_uids:
            log.info("Server does not support UIDPLUS -- %d originals marked as \\Deleted "
                     "(will be removed on next folder compaction).",
                     len(deleted_uids))

    except Exception as e:
        result["errors"].append(f"IMAP connection error: {e}")
        log.warning("IMAP auto-triage connection error: %s", e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return result
