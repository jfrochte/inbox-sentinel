"""
smtp_client.py -- SMTP email sending.

Leaf module with no internal package dependencies.
Handles sending the finished report email via SMTP (with TLS/STARTTLS or SSL).
"""

# ============================================================
# External dependencies
# ============================================================
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ============================================================
# SMTP sending (explicit envelope-from + SSL/TLS for port 465)
# ============================================================
def send_email_html(username: str, password: str, from_email: str, recipient_email: str,
                    subject: str, html_content: str, plain_text: str,
                    smtp_server: str, smtp_port: int, smtp_ssl: bool) -> None:
    """
    Sends via SMTP.

    - Sets both the "From" header and the envelope-from explicitly,
      so SMTP servers are less likely to reject the message.
    - Uses SMTP_SSL for port 465 instead of starttls().
    - Multipart/alternative (text/plain + text/html) for proper rendering.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    # Plain first, then HTML
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
