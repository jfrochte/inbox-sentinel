# inbox-sentinel

> **LLM-powered email triage** -- a local Ollama model reads your inbox and sends you a prioritized daily report.

> **LLM-gestuetzte E-Mail-Triage** -- ein lokales Ollama-Modell liest die Inbox und verschickt einen priorisierten Tagesbericht.

---

## WARNING / WARNUNG

**This project is in a very early stage of development (alpha). It is NOT production-ready.**

- Use ONLY with a **dedicated test email account** (dummy account).
- Do NOT run this against your real inbox where losing or misplacing emails would be a problem.
- The auto-sort feature moves emails between IMAP folders. While it does not delete emails, a bug could place emails in unexpected folders or change read/unread flags.
- LLM output is non-deterministic. Classifications (SPAM, PHISHING, FYI, ACTIONABLE) may be wrong.
- **No warranty.** See [LICENSE](LICENSE).

---

**Dieses Projekt befindet sich in einem sehr fruehen Entwicklungsstadium (Alpha). Es ist NICHT produktionsreif.**

- Nur mit einem **dedizierten Test-E-Mail-Account** (Dummy-Account) verwenden.
- NICHT auf die echte Inbox anwenden, wenn verlorene oder verschobene E-Mails ein Problem waeren.
- Die Auto-Sort-Funktion verschiebt E-Mails zwischen IMAP-Ordnern. Es werden keine E-Mails geloescht, aber ein Fehler koennte E-Mails in unerwartete Ordner verschieben oder Gelesen/Ungelesen-Flags aendern.
- LLM-Ausgaben sind nicht-deterministisch. Klassifikationen (SPAM, PHISHING, FYI, ACTIONABLE) koennen falsch sein.
- **Keine Garantie.** Siehe [LICENSE](LICENSE).

---

## What it does / Was es tut

**English:**
inbox-sentinel connects to an IMAP mailbox, fetches recent emails, and sends each one to a local LLM (via Ollama) for analysis. The LLM classifies each email by category, priority, and addressing. The results are compiled into an HTML report that is emailed back to you. Optionally, emails classified as SPAM, PHISHING, or FYI are auto-sorted into IMAP subfolders.

**Deutsch:**
inbox-sentinel verbindet sich mit einer IMAP-Mailbox, holt aktuelle E-Mails und schickt jede einzelne an ein lokales LLM (via Ollama) zur Analyse. Das LLM klassifiziert jede E-Mail nach Kategorie, Prioritaet und Adressierung. Die Ergebnisse werden in einen HTML-Report kompiliert und per E-Mail zurueckgeschickt. Optional werden E-Mails der Kategorien SPAM, PHISHING oder FYI automatisch in IMAP-Unterordner sortiert.

### Key features / Kernfunktionen

- **Local LLM** -- your emails never leave your machine (Ollama runs locally)
- **Priority 1-5** -- urgent items surface first in the report
- **Categories** -- SPAM, PHISHING, FYI, ACTIONABLE with deterministic post-processing rules enforced in code
- **Auto-sort** -- optional IMAP folder sorting by category (Spam, Quarantine, FYI)
- **Profiles** -- save/load server and account settings as JSON profiles
- **Cross-platform** -- bootstrap scripts for Linux/macOS (sh) and Windows (PowerShell)

---

- **Lokales LLM** -- E-Mails verlassen nie den eigenen Rechner (Ollama laeuft lokal)
- **Prioritaet 1-5** -- dringende Eintraege erscheinen zuerst im Report
- **Kategorien** -- SPAM, PHISHING, FYI, ACTIONABLE mit deterministischen Post-Processing-Regeln im Code
- **Auto-Sort** -- optionale IMAP-Ordner-Sortierung nach Kategorie (Spam, Quarantine, FYI)
- **Profile** -- Server- und Account-Einstellungen als JSON-Profile speichern/laden
- **Plattformuebergreifend** -- Bootstrap-Skripte fuer Linux/macOS (sh) und Windows (PowerShell)

## Requirements / Voraussetzungen

- Python 3.10+
- [Ollama](https://ollama.com/) running locally with a model (default: `qwen2.5:7b-instruct-q8_0`)
- An IMAP/SMTP email account (for testing: use a dedicated dummy account)

## Quick start / Schnellstart

```bash
# 1. Clone
git clone https://github.com/jfrochte/inbox-sentinel.git
cd inbox-sentinel

# 2. Bootstrap (creates venv, installs dependencies)
./bootstrap.sh        # Linux / macOS
# .\bootstrap.ps1     # Windows PowerShell

# 3. Activate venv
source .venv/bin/activate

# 4. Make sure Ollama is running with a model
ollama pull qwen2.5:7b-instruct-q8_0

# 5. Run
python -m email_report
```

The interactive prompt will guide you through server settings, account details, and profile management.

---

```bash
# 1. Klonen
git clone https://github.com/jfrochte/inbox-sentinel.git
cd inbox-sentinel

# 2. Bootstrap (erstellt venv, installiert Abhaengigkeiten)
./bootstrap.sh        # Linux / macOS
# .\bootstrap.ps1     # Windows PowerShell

# 3. venv aktivieren
source .venv/bin/activate

# 4. Ollama mit Modell starten
ollama pull qwen2.5:7b-instruct-q8_0

# 5. Ausfuehren
python -m email_report
```

Der interaktive Prompt fuehrt durch Server-Einstellungen, Account-Daten und Profilverwaltung.

## Architecture / Architektur

```
email_report/
  config.py       -- Config dataclass, profile save/load
  interactive.py   -- All user prompts (profile selection, settings)
  imap_client.py   -- IMAP fetch (read-only) and auto-sort (move)
  email_parser.py  -- MIME decoding, body extraction
  llm.py           -- Ollama interaction, validation, repair pass,
                      deterministic addressing detection & post-processing
  report.py        -- Sorting, HTML generation, block parsing
  smtp_client.py   -- Send HTML report via SMTP
  main.py          -- Orchestration (glue between all modules)
  utils.py         -- Logging, file helpers

prompt.txt         -- LLM system prompt (English, output in German)
profiles/          -- Saved JSON profiles (excluded from git)
```

### Processing pipeline / Verarbeitungspipeline

1. Load profile or configure interactively
2. IMAP: fetch emails (read-only, `BODY.PEEK[]`)
3. Per email: LLM analysis -> validate -> repair if needed -> fallback
4. Deterministic post-processing (addressing, priority caps, SPAM/PHISHING enforcement)
5. Sort results by priority, generate HTML report
6. Send report via SMTP
7. Auto-sort emails into IMAP folders (optional, after report is sent)

## Configuration / Konfiguration

All settings are prompted interactively on first run. Profiles are saved to `profiles/` as JSON (passwords are never stored).

Key environment variables:

| Variable | Description |
|---|---|
| `EMAIL_REPORT_DEBUG` | Keep temp files after run (`1`/`true`) |
| `EMAIL_REPORT_DEBUG_LOG` | Write debug JSONL log (`1`/`true`) |
| `EMAIL_REPORT_LOGLEVEL` | `INFO` or `DEBUG` |
| `OLLAMA_URL` | Ollama API endpoint (default: `http://localhost:11434/api/generate`) |

## Known limitations / Bekannte Einschraenkungen

**English:**
- LLM classifications are not reliable -- the model may miscategorize emails. Deterministic rules in code correct the most critical cases (SPAM/PHISHING priority, self-sent detection, addressing), but category assignment still depends on the LLM.
- Auto-sort uses IMAP copy+delete+expunge. On servers with UIDPLUS support (RFC 4315), only our specific messages are expunged. On servers without UIDPLUS, a regular EXPUNGE is used which could also remove other messages previously flagged as deleted.
- The prompt and deterministic output fields (Summary, Context, Actions) are in German. The prompt instructions are in English.
- Tested primarily with Dovecot-based IMAP servers. Other servers may behave differently.

**Deutsch:**
- LLM-Klassifikationen sind nicht zuverlaessig -- das Modell kann E-Mails falsch einordnen. Deterministische Regeln im Code korrigieren die kritischsten Faelle (SPAM/PHISHING-Prioritaet, Self-Sent-Erkennung, Addressing), aber die Kategorie-Zuordnung haengt weiterhin vom LLM ab.
- Auto-Sort nutzt IMAP Copy+Delete+Expunge. Bei Servern mit UIDPLUS-Unterstuetzung (RFC 4315) werden nur unsere spezifischen Nachrichten entfernt. Bei Servern ohne UIDPLUS wird ein regulaeres EXPUNGE verwendet, das auch andere zuvor als geloescht markierte Nachrichten entfernen koennte.
- Der Prompt und die deterministischen Ausgabefelder (Summary, Context, Actions) sind auf Deutsch. Die Prompt-Anweisungen sind auf Englisch.
- Primaer mit Dovecot-basierten IMAP-Servern getestet. Andere Server koennten sich anders verhalten.

## License / Lizenz

[MIT](LICENSE)
