"""
smtp_client.py – SMTP-Versand von E-Mails.

Dieses Modul ist ein Blattmodul ohne interne Paket-Abhaengigkeiten.
Es kuemmert sich ausschliesslich um den Versand der fertigen Report-Mail
ueber SMTP (mit TLS/STARTTLS oder SSL).
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ============================================================
# SMTP Versand (Punkt 7: Envelope-From sauber + SSL/TLS 465)
# ============================================================
def send_email_html(username: str, password: str, from_email: str, recipient_email: str,
                    subject: str, html_content: str, plain_text: str,
                    smtp_server: str, smtp_port: int, smtp_ssl: bool) -> None:
    """
    Versand ueber SMTP.

    Punkt 7:
    - Wir setzen nicht nur Header "From", sondern auch Envelope-From explizit,
      damit SMTP-Server weniger "komisch" reagieren.
    - Bei 465 nutzen wir SMTP_SSL statt starttls().

    Zusaetzlich:
    - Multipart/alternative (text/plain + text/html), damit Mail-Clients sauber rendern.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    # Plain zuerst, dann HTML
    msg.attach(MIMEText(plain_text or "", "plain", "utf-8"))
    msg.attach(MIMEText(html_content or "", "html", "utf-8"))

    if smtp_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)

    try:
        server.ehlo()

        if not smtp_ssl:
            server.starttls()
            server.ehlo()

        server.login(username, password)

        server.send_message(msg, from_addr=from_email, to_addrs=[recipient_email])
        print("E-Mail wurde gesendet.")
    finally:
        try:
            server.quit()
        except Exception:
            pass
