"""
organizations.py – Organisations-Presets fuer IMAP/SMTP-Server.

Blattmodul ohne interne Paket-Abhaengigkeiten.
Enthaelt nur oeffentliche Server-Adressen und Ports – keine Geheimnisse.
"""

# ============================================================
# Organisations-Presets
# ============================================================
ORGANIZATIONS = [
    {
        "key": "hs-bochum",
        "label": "Hochschule Bochum",
        "imap_server": "mail.hs-bochum.de",
        "imap_port": 993,
        "smtp_server": "mail.hs-bochum.de",
        "smtp_port": 587,
        "smtp_ssl": False,
    },
    {
        "key": "gmail",
        "label": "Gmail",
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    {
        "key": "outlook",
        "label": "Outlook / Microsoft 365",
        "imap_server": "outlook.office365.com",
        "imap_port": 993,
        "smtp_server": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_ssl": False,
    },
    {
        "key": "ionos",
        "label": "IONOS (Deutschland)",
        "imap_server": "imap.ionos.de",
        "imap_port": 993,
        "smtp_server": "smtp.ionos.de",
        "smtp_port": 587,
        "smtp_ssl": False,
    },
]


def get_organization(key: str) -> dict | None:
    """Gibt das Preset-Dict fuer den gegebenen Key zurueck, oder None."""
    for org in ORGANIZATIONS:
        if org["key"] == key:
            return org
    return None
