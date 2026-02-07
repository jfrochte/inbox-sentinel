"""
imap_client.py – IMAP-Verbindung und E-Mail-Abruf.

Abhaengigkeiten innerhalb des Pakets:
  - utils (Logging)
  - email_parser (Header-Dekodierung, Body-Extraktion)

Wichtige Aenderung gegenueber der monolithischen Version:
  skip_own_sent ist jetzt ein expliziter Parameter statt eines globalen Flags
  (SKIP_OWN_SENT_MAILS). Der Wert wird aus config.skip_own_sent uebergeben.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import imaplib
import email
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
# IMAP Abruf (Punkt 6: optional SENT* Suche)
# ============================================================
def imap_fetch_emails_for_range(username: str, password: str, from_email: str, days_back: int,
                                imap_server: str, imap_port: int, mailbox: str,
                                use_sentdate: bool, skip_own_sent: bool = True):
    """
    Liefert Liste von dicts:
    {
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

        status, data = mail.search(None, query)
        if status != "OK":
            log.warning("IMAP search failed. status=%s query=%s", status, query)
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        iterator = msg_ids
        if tqdm is not None:
            iterator = tqdm(msg_ids, desc="Download E-Mails")

        for msg_id in iterator:
            typ, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
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
