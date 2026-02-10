"""
imap_client.py – IMAP-Verbindung und E-Mail-Abruf.

Abhaengigkeiten innerhalb des Pakets:
  - utils (Logging)
  - email_parser (Header-Dekodierung, Body-Extraktion)

Wichtige Aenderung gegenueber der monolithischen Version:
  skip_own_sent ist jetzt ein expliziter Parameter statt eines globalen Flags
  (SKIP_OWN_SENT_MAILS). Der Wert wird aus config.skip_own_sent uebergeben.

UID-basierter Fetch: Statt Sequenznummern werden UIDs verwendet,
da diese ueber Sessions hinweg stabil sind (noetig fuer Auto-Sort).
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import imaplib
import email
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

# tqdm ist optional: wenn installiert, gibt es Fortschrittsbalken.
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import log
from email_report.email_parser import (
    decode_mime_words,
    get_email_address_from_header,
    extract_best_body_text,
)


# ============================================================
# IMAP Abruf (UID-basiert, Punkt 6: optional SENT* Suche)
# ============================================================
def imap_fetch_emails_for_range(username: str, password: str, from_email: str, days_back: int,
                                imap_server: str, imap_port: int, mailbox: str,
                                use_sentdate: bool, skip_own_sent: bool = True):
    """
    Liefert Liste von dicts:
    {
      'uid': ...,
      'subject': ...,
      'from': ...,
      'from_addr': ...,
      'to': ...,
      'cc': ...,
      'body': ...,
    }

    Der Zeitraum wird als [heute - days_back, morgen) definiert, also inklusiv heute und der letzten days_back Tage.

    Suchlogik:
    - use_sentdate True: (SENTSINCE <start> SENTBEFORE <end_excl>)
      basiert auf "Date:" Header (Sendedatum)
    - use_sentdate False: (SINCE <start> BEFORE <end_excl>)
      basiert auf INTERNALDATE (Ablagezeit)

    skip_own_sent: Wenn True, werden eigene gesendete Mails uebersprungen.
    (Ersetzt das fruehere globale SKIP_OWN_SENT_MAILS.)
    """
    # Zeitraum:
    # days_back = 0  -> nur heute
    # days_back = 2  -> heute + die letzten 2 Tage (insgesamt 3 Kalendertage)
    start_day = (datetime.now() - timedelta(days=days_back)).date()      # inklusiv
    end_day_excl = datetime.now().date() + timedelta(days=1)            # exklusiv (morgen)

    since_str = start_day.strftime("%d-%b-%Y")
    before_str = end_day_excl.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    emails = []

    try:
        mail.login(username, password)

        # read-only, damit "Seen" nicht gesetzt wird (sofern Server das respektiert)
        try:
            mail.select(mailbox, readonly=True)
        except Exception:
            mail.select(mailbox)

        if use_sentdate:
            # SENTSINCE/SENTBEFORE sind standardisiert, aber nicht jeder Server ist 100% kompatibel.
            query = f"(SENTSINCE {since_str} SENTBEFORE {before_str})"
        else:
            query = f"(SINCE {since_str} BEFORE {before_str})"

        status, data = mail.uid('search', None, query)
        if status != "OK":
            log.warning("IMAP UID search failed. status=%s query=%s", status, query)
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        iterator = msg_ids
        if tqdm is not None:
            iterator = tqdm(msg_ids, desc="Download E-Mails")

        for uid_bytes in iterator:
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

            # skip_own_sent: expliziter Parameter statt globalem Flag
            if skip_own_sent and from_addr and from_addr == from_email.lower():
                continue

            subject = decode_mime_words(message.get("subject"))
            to_header = decode_mime_words(message.get("to"))
            cc_header = decode_mime_words(message.get("cc"))

            # Threading-Header extrahieren
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
            })

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return emails


# ============================================================
# IMAP Auto-Sort: Crash-sichere Nachbearbeitung
# ============================================================
def _check_keyword_support(mail) -> bool:
    """Prueft ob die Mailbox benutzerdefinierte Keywords (Flags) unterstuetzt.

    PERMANENTFLAGS mit '\\*' bedeutet: Server erlaubt beliebige Keywords.

    imaplib speichert PERMANENTFLAGS als Teil der OK-Responses (nicht als
    eigenen Key), daher muessen wir mail.response("OK") durchsuchen.
    """
    try:
        # PERMANENTFLAGS kommt als untagged OK response von SELECT:
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


def _is_already_tagged(mail, uid_bytes, keyword: str) -> bool:
    """Prueft ob eine UID bereits das angegebene Keyword-Flag traegt."""
    try:
        status, data = mail.uid('fetch', uid_bytes, '(FLAGS)')
        if status != "OK" or not data or not data[0]:
            return False
        flags_raw = data[0]
        if isinstance(flags_raw, tuple):
            flags_raw = flags_raw[1]
        if isinstance(flags_raw, bytes):
            flags_raw = flags_raw.decode("utf-8", errors="replace")
        return keyword in str(flags_raw)
    except Exception:
        return False


def _tag_as_sorted(mail, uid_bytes, keyword: str) -> bool:
    """Setzt das Keyword-Flag auf einer UID. Gibt True bei Erfolg zurueck."""
    try:
        status, _ = mail.uid('store', uid_bytes, '+FLAGS', f'({keyword})')
        return status == "OK"
    except Exception as e:
        log.debug("Keyword-Tag fehlgeschlagen fuer UID %s: %s", uid_bytes, e)
        return False


def _inject_x_priority(raw_bytes: bytes, priority: int) -> bytes:
    """Injiziert einen X-Priority Header in eine rohe IMAP-Nachricht.

    Fuegt den Header vor dem Ende des Header-Blocks ein (vor \\r\\n\\r\\n).
    Falls X-Priority schon vorhanden ist, wird nichts geaendert.
    """
    # Header-Ende finden
    header_end = raw_bytes.find(b"\r\n\r\n")
    sep = b"\r\n"
    if header_end < 0:
        header_end = raw_bytes.find(b"\n\n")
        sep = b"\n"
    if header_end < 0:
        return raw_bytes  # Kein Header/Body-Separator gefunden

    # Duplikat vermeiden
    header_section = raw_bytes[:header_end]
    if b"X-Priority:" in header_section or b"x-priority:" in header_section.lower():
        return raw_bytes

    x_prio = sep + f"X-Priority: {priority}".encode()
    return raw_bytes[:header_end] + x_prio + raw_bytes[header_end:]


def _fetch_raw_with_date(mail, uid_bytes):
    """Holt rohe Nachricht + INTERNALDATE per UID FETCH.

    Returns: (raw_bytes, internaldate_str) oder (None, None) bei Fehler.
    """
    try:
        status, data = mail.uid('fetch', uid_bytes, '(BODY.PEEK[] INTERNALDATE)')
        if status != "OK" or not data:
            return None, None

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
            return None, None

        # INTERNALDATE aus Metadaten extrahieren
        internaldate = None
        m = re.search(r'INTERNALDATE "([^"]+)"', meta_str)
        if m:
            internaldate = m.group(1)

        return raw_bytes, internaldate
    except Exception as e:
        log.debug("FETCH fehlgeschlagen fuer UID %s: %s", uid_bytes, e)
        return None, None


def imap_safe_sort(username: str, password: str, imap_server: str, imap_port: int,
                   mailbox: str, sort_moves: list[dict],
                   inbox_flags: list[dict] | None = None) -> dict:
    """
    Crash-sichere IMAP-Nachbearbeitung in einer Session.

    sort_moves: [{"uid": str, "folder": str, "priority": int}]
        FETCH → X-Priority injizieren → APPEND in Zielordner → verify →
        $Sentinel_Sorted taggen → \\Seen + \\Deleted auf Original →
        UID EXPUNGE (nur mit UIDPLUS, sonst nur \\Deleted).

    inbox_flags: [{"uid": str, "action": "seen"|"flagged"}]
        STORE \\Seen (FYI) oder \\Flagged (ACTIONABLE Prio 1-2) →
        $Sentinel_Sorted taggen.

    Sicherheitsgarantien:
    - Kein Email-Verlust: Original wird erst geloescht wenn Kopie verifiziert
    - Crash-sicher: Abbruch → schlimmstenfalls Duplikat im Zielordner
    - Idempotent: $Sentinel_Sorted verhindert Doppelverarbeitung
    - Server-kompatibel: ohne UIDPLUS nur \\Deleted (kein EXPUNGE)

    Gibt {"sorted": int, "flagged": int, "skipped": int, "failed": int,
          "errors": [str], "keywords_supported": bool, "has_uidplus": bool}
    zurueck.
    """
    from email_report.config import SENTINEL_KEYWORD

    result = {"sorted": 0, "flagged": 0, "skipped": 0, "failed": 0,
              "errors": [], "keywords_supported": False, "has_uidplus": False}

    if not sort_moves and not inbox_flags:
        return result
    inbox_flags = inbox_flags or []

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(username, password)

        # UIDPLUS-Faehigkeit pruefen (fuer sicheres UID EXPUNGE)
        has_uidplus = False
        try:
            caps = mail.capabilities or ()
            has_uidplus = b"UIDPLUS" in caps or "UIDPLUS" in caps
        except Exception:
            pass
        result["has_uidplus"] = has_uidplus

        # Quell-Mailbox read-write oeffnen
        status, _ = mail.select(mailbox, readonly=False)
        if status != "OK":
            result["errors"].append(f"Konnte Mailbox '{mailbox}' nicht oeffnen")
            return result

        # PERMANENTFLAGS pruefen: Keywords supported?
        kw_supported = _check_keyword_support(mail)
        result["keywords_supported"] = kw_supported

        # --- Phase 1: Zielordner erstellen ---
        created_folders = set()
        for move in sort_moves:
            folder_name = move.get("folder", "")
            if not folder_name or folder_name in created_folders:
                continue
            try:
                status_c, _ = mail.create(folder_name)
                if status_c == "OK":
                    log.info("IMAP-Ordner erstellt: %s", folder_name)
            except Exception:
                pass  # Ordner existiert vermutlich schon
            try:
                mail.subscribe(folder_name)
            except Exception:
                pass
            created_folders.add(folder_name)

        # --- Phase 2: Sort-Moves (SPAM/PHISHING) ---
        # FETCH → X-Priority → APPEND → verify → tag → \Seen → \Deleted
        deleted_uids = []  # Fuer Batch-EXPUNGE am Ende

        for move in sort_moves:
            uid = move.get("uid", "")
            folder_name = move.get("folder", "")
            priority = move.get("priority", 5)
            if not uid or not folder_name:
                continue

            uid_b = uid.encode() if isinstance(uid, str) else uid

            try:
                # Idempotenz: bereits getaggte Mails ueberspringen
                if kw_supported and _is_already_tagged(mail, uid_b, SENTINEL_KEYWORD):
                    result["skipped"] += 1
                    continue

                # FETCH rohe Nachricht + INTERNALDATE
                raw_bytes, internaldate = _fetch_raw_with_date(mail, uid_b)
                if not raw_bytes:
                    result["failed"] += 1
                    result["errors"].append(f"UID {uid}: FETCH fehlgeschlagen")
                    continue

                # X-Priority Header injizieren
                modified = _inject_x_priority(raw_bytes, priority)

                # APPEND in Zielordner (mit \Seen + Original-Datum)
                status_a, _ = mail.append(
                    folder_name,
                    "(\\Seen)",
                    internaldate,
                    modified,
                )
                if status_a != "OK":
                    result["failed"] += 1
                    result["errors"].append(f"UID {uid}: APPEND nach {folder_name} fehlgeschlagen")
                    continue

                # --- Kopie verifiziert, jetzt Original aufraumen ---

                # $Sentinel_Sorted taggen (Idempotenz fuer naechsten Lauf)
                if kw_supported:
                    _tag_as_sorted(mail, uid_b, SENTINEL_KEYWORD)

                # \Seen auf Original (gelesen in INBOX)
                try:
                    mail.uid('store', uid_b, '+FLAGS', '(\\Seen)')
                except Exception:
                    pass

                # \Deleted auf Original
                try:
                    mail.uid('store', uid_b, '+FLAGS', '(\\Deleted)')
                    deleted_uids.append(uid)
                except Exception:
                    pass

                result["sorted"] += 1

            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"UID {uid}: {e}")
                log.warning("Sort-Fehler fuer UID %s nach %s: %s", uid, folder_name, e)

        # --- Phase 3: Inbox-Flags (FYI → \Seen, ACTIONABLE Prio 1-2 → \Flagged) ---
        for flag_op in inbox_flags:
            uid = flag_op.get("uid", "")
            action = flag_op.get("action", "")
            if not uid or not action:
                continue

            uid_b = uid.encode() if isinstance(uid, str) else uid

            try:
                # Idempotenz: bereits getaggte Mails ueberspringen
                if kw_supported and _is_already_tagged(mail, uid_b, SENTINEL_KEYWORD):
                    result["skipped"] += 1
                    continue

                if action == "seen":
                    mail.uid('store', uid_b, '+FLAGS', '(\\Seen)')
                elif action == "flagged":
                    mail.uid('store', uid_b, '+FLAGS', '(\\Flagged)')

                # $Sentinel_Sorted taggen
                if kw_supported:
                    _tag_as_sorted(mail, uid_b, SENTINEL_KEYWORD)

                result["flagged"] += 1

            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"UID {uid} ({action}): {e}")
                log.warning("Flag-Fehler fuer UID %s (%s): %s", uid, action, e)

        # --- Phase 4: Batch-EXPUNGE (nur mit UIDPLUS) ---
        if deleted_uids and has_uidplus:
            uid_set = ",".join(deleted_uids)
            try:
                mail.uid('expunge', uid_set)
            except Exception as e:
                log.warning("UID EXPUNGE fehlgeschlagen (Originale bleiben als "
                            "\\Deleted markiert): %s", e)
        elif deleted_uids:
            log.info("Server hat kein UIDPLUS – %d Originale als \\Deleted markiert "
                     "(werden beim naechsten Ordner-Komprimieren entfernt).",
                     len(deleted_uids))

    except Exception as e:
        result["errors"].append(f"IMAP-Verbindungsfehler: {e}")
        log.warning("IMAP Auto-Sort Verbindungsfehler: %s", e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return result
