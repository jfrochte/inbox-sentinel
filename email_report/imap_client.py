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

            body = extract_best_body_text(message)

            emails.append({
                "uid": uid_bytes.decode(),
                "subject": subject,
                "from": from_header,
                "from_addr": from_addr,
                "to": to_header,
                "cc": cc_header,
                "body": body,
            })

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return emails


# ============================================================
# IMAP Auto-Sort: E-Mails in Unterordner verschieben
# ============================================================
def imap_move_emails(username: str, password: str, imap_server: str, imap_port: int,
                     mailbox: str, moves: list[dict]) -> dict:
    """
    Verschiebt E-Mails per UID in IMAP-Unterordner.

    moves: Liste von {"uid": "...", "folder": "Spam"/"Quarantine"/"FYI"}

    Ablauf pro E-Mail:
    1) Ordner-Separator via mail.list() erkennen
    2) Unterordner erstellen wenn noetig (mail.create)
    3) mail.uid('copy', uid, zielordner)
    4) mail.uid('store', uid, '+FLAGS', '(\\Deleted)')
    5) UID EXPUNGE (nur unsere UIDs) oder Fallback auf regulaeres EXPUNGE

    Sicherheit: Nutzt UID EXPUNGE (UIDPLUS, RFC 4315) wenn verfuegbar,
    damit nur die von uns geflaggten Mails entfernt werden und keine
    vorher schon als \\Deleted markierten Mails verloren gehen.

    Gibt {"moved": int, "failed": int, "errors": [str]} zurueck.
    """
    result = {"moved": 0, "failed": 0, "errors": []}

    if not moves:
        return result

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

        # Ordner-Separator erkennen (z.B. "/" oder ".")
        separator = "/"
        try:
            status, folder_list = mail.list()
            if status == "OK" and folder_list:
                first = folder_list[0]
                if isinstance(first, bytes):
                    first = first.decode("utf-8", errors="replace")
                # Format: '(\\HasNoChildren) "/" INBOX'
                m = re.search(r'\) "(.)" ', first)
                if m:
                    separator = m.group(1)
        except Exception as e:
            log.debug("Konnte Ordner-Separator nicht ermitteln, nutze '/': %s", e)

        # Quell-Mailbox read-write oeffnen
        status, _ = mail.select(mailbox, readonly=False)
        if status != "OK":
            result["errors"].append(f"Konnte Mailbox '{mailbox}' nicht oeffnen")
            return result

        # Bekannte Zielordner sammeln und ggf. erstellen
        created_folders = set()
        for move in moves:
            folder_name = move.get("folder", "")
            if not folder_name or folder_name in created_folders:
                continue

            # Zielordner auf gleicher Ebene wie INBOX (Top-Level)
            target = folder_name

            try:
                status_c, _ = mail.create(target)
                if status_c == "OK":
                    log.info("IMAP-Ordner erstellt: %s", target)
            except Exception:
                pass  # Ordner existiert vermutlich schon
            # Subscribe: ohne Abo zeigen viele Clients den Ordner nicht an
            try:
                mail.subscribe(target)
            except Exception:
                pass
            created_folders.add(folder_name)

        # E-Mails verschieben
        deleted_uids = []  # UIDs die wir als \Deleted markiert haben
        for move in moves:
            uid = move.get("uid", "")
            folder_name = move.get("folder", "")
            if not uid or not folder_name:
                continue

            target = folder_name
            uid_b = uid.encode() if isinstance(uid, str) else uid

            try:
                # Copy to target folder
                status, _ = mail.uid('copy', uid_b, target)
                if status != "OK":
                    result["failed"] += 1
                    result["errors"].append(f"UID {uid}: copy nach {target} fehlgeschlagen")
                    continue

                # Mark as deleted in source
                mail.uid('store', uid_b, '+FLAGS', '(\\Deleted)')
                deleted_uids.append(uid)
                result["moved"] += 1

            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"UID {uid}: {e}")
                log.warning("Fehler beim Verschieben von UID %s nach %s: %s", uid, target, e)

        # Expunge: nur unsere UIDs entfernen
        if deleted_uids:
            uid_set = ",".join(deleted_uids)
            if has_uidplus:
                try:
                    mail.uid('expunge', uid_set)
                except Exception as e:
                    log.warning("UID EXPUNGE fehlgeschlagen, Fallback auf EXPUNGE: %s", e)
                    try:
                        mail.expunge()
                    except Exception as e2:
                        log.warning("Expunge fehlgeschlagen: %s", e2)
            else:
                log.info("Server hat kein UIDPLUS – nutze regulaeres EXPUNGE. "
                         "Bereits vorher als Deleted markierte Mails koennten mit-entfernt werden.")
                try:
                    mail.expunge()
                except Exception as e:
                    log.warning("Expunge fehlgeschlagen: %s", e)

    except Exception as e:
        result["errors"].append(f"IMAP-Verbindungsfehler: {e}")
        log.warning("IMAP Auto-Sort Verbindungsfehler: %s", e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return result
