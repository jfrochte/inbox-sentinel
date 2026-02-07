"""
email_report.py – Duenner Backward-Compatibility-Wrapper.

Leitet den Aufruf an das neue email_report-Paket weiter.
Damit funktionieren run.sh und run.ps1 weiterhin unveraendert mit:
    python email_report.py
"""

from email_report.main import main

if __name__ == "__main__":
    main()
