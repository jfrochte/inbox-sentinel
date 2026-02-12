# inbox-sentinel

> **LLM-powered email triage** -- a local Ollama model reads your inbox and sends you a prioritized daily report.

This README is bilingual: **English first**, then **Deutsch**.  
Jump to: [English](#english) | [Deutsch](#deutsch)

---

## English

### WARNING

**This project is in a very early stage of development (alpha). It is NOT production-ready.**

- Use ONLY with a **dedicated test email account** (dummy account).
- Do NOT run this against your real inbox where losing or misplacing emails would be a problem.
- The auto-triage feature copies emails to IMAP folders and sets flags (starred, deleted). A crash-safe design ensures no email is ever lost (copy is verified before the original is marked for deletion), but a bug could place emails in unexpected folders or change flags.
- LLM output is non-deterministic. Classifications (SPAM, PHISHING, FYI, ACTIONABLE) may be wrong.
- **No warranty.** See [LICENSE](LICENSE).

### What it does

inbox-sentinel connects to an IMAP mailbox, fetches recent emails, groups them into conversation threads, and sends each thread to a local LLM (via Ollama) for analysis. The LLM classifies each thread by category, priority, and addressing. The results are compiled into an HTML report that is emailed back to you. Optionally, all emails receive an X-Priority header (visible in Thunderbird, Outlook, Apple Mail), SPAM/PHISHING emails are moved to quarantine folders, high-priority emails get a star/flag, LLM-generated reply drafts are saved to your Drafts folder, and a per-sender knowledge base is built to provide context for future analyses.

### Key features

- **Local LLM** -- your emails never leave your machine (Ollama runs locally)
- **Threading** -- emails are grouped into conversation threads (Union-Find on headers + subject fallback)
- **Priority 1-5** -- urgent items surface first in the report
- **Categories** -- SPAM, PHISHING, FYI, ACTIONABLE with deterministic post-processing rules enforced in code
- **Auto-triage** -- crash-safe IMAP post-processing: every email gets an X-Priority header injected (1-5, visible in most clients). SPAM/PHISHING moved to quarantine, high-priority ACTIONABLE starred. Uses verified copy-before-delete; re-running with a better model overwrites previous priorities
- **Auto-draft** -- optional LLM-generated reply drafts saved to your IMAP Drafts folder (auto-detects `\Drafts` special-use folder per RFC 6154, `[Sentinel-Entwurf]` subject prefix, opt-in)
- **Auto-contacts** -- optional sender knowledge base built from emails; provides context to analysis and draft prompts (opt-in)
- **Profiles** -- save/load server and account settings as JSON profiles
- **Cross-platform** -- bootstrap scripts for Linux/macOS (sh) and Windows (PowerShell)

### Prerequisites

#### 1. Python 3.10+

Download: [python.org](https://www.python.org/downloads/). Make sure `python3` (or `python`) and `pip` are available in your terminal.

#### 2. Ollama (local LLM server)

inbox-sentinel uses [Ollama](https://ollama.com/) to run a language model locally on your machine. No data is sent to external servers.

**Install Ollama:**
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **macOS:** Download from [ollama.com/download](https://ollama.com/download) or `brew install ollama`
- **Windows:** Download from [ollama.com/download](https://ollama.com/download)

**Download a model:**

```bash
ollama pull gpt-os-20b
```

This downloads ~8 GB. Other instruction-tuned models also work (e.g. `llama3.1:8b-instruct-q8_0`), but the prompt is optimized for `qwen2.5`. Ollama must be running before you start inbox-sentinel (the `run.sh`/`run.ps1` scripts will try to start it automatically).

#### 3. A test email account

You need an IMAP/SMTP email account. **Use a dedicated dummy account for testing** -- do not use your real inbox (see [warning above](#warning)).

### Installation

```bash
# 1. Clone
git clone https://github.com/jfrochte/inbox-sentinel.git
cd inbox-sentinel

# 2. Bootstrap (creates venv + installs Python dependencies)
./bootstrap.sh        # Linux / macOS
# .\bootstrap.ps1    # Windows PowerShell
```

### Usage

```bash
# Option A: Use the run script (checks Ollama, activates venv)
./run.sh              # Linux / macOS
# .\run.ps1          # Windows PowerShell

# Option B: Run directly (venv must be active, Ollama must be running)
source .venv/bin/activate
python -m email_report
```

The interactive prompt will guide you through server settings, account details, and profile management. On the first run all settings are prompted; on subsequent runs a saved profile can be loaded.

### Architecture

```
email_report/
  config.py        -- Config dataclass, profile save/load
  interactive.py   -- All user prompts (profile selection, settings)
  imap_client.py   -- IMAP fetch (read-only) and crash-safe auto-triage
  email_parser.py  -- MIME decoding, body extraction
  threading.py     -- Thread grouping (Union-Find on headers + subject fallback)
  llm.py           -- Ollama interaction, validation, repair pass,
                      deterministic addressing detection & post-processing
  report.py        -- Sorting, HTML generation, block parsing
  smtp_client.py   -- Send HTML report via SMTP
  drafts.py        -- Auto-draft: LLM reply drafts, IMAP APPEND
  contacts.py      -- Auto-contacts: per-sender JSON profiles, LLM extraction
  main.py          -- Orchestration (glue between all modules)
  utils.py         -- Logging, file helpers

prompt.txt             -- LLM analysis prompt (English instructions, German output)
draft_prompt.txt       -- LLM draft generation prompt
contact_prompt.txt     -- LLM contact extraction prompt
profiles/              -- Saved JSON profiles (excluded from git)
contacts/              -- Per-sender JSON profiles (excluded from git)
```

### Processing pipeline

1. Load profile or configure interactively
2. IMAP: fetch emails (read-only, `BODY.PEEK[]`)
3. Group emails into conversation threads (Union-Find)
4. Per thread: load sender contact -> LLM analysis -> validate -> repair if needed -> fallback
5. Deterministic post-processing (addressing, priority caps, SPAM/PHISHING enforcement)
6. Per thread (optional): generate LLM reply draft, update sender contact
7. Sort results by priority, generate HTML report
8. Send report via SMTP
9. Save drafts to IMAP Drafts folder (auto-detects `\Drafts` special-use, optional)
10. Auto-triage: all emails get X-Priority injected via FETCH + APPEND + verify + delete. SPAM/PHISHING → quarantine + `\Seen`, FYI → X-Priority only, ACTIONABLE prio 1-2 → `\Flagged`. Re-running overwrites priorities (optional)

### Configuration

All settings are prompted interactively on first run. Profiles are saved to `profiles/` as JSON (passwords are never stored).

Key environment variables:

| Variable | Description |
|---|---|
| `EMAIL_REPORT_DEBUG` | Keep temp files after run (`1`/`true`) |
| `EMAIL_REPORT_DEBUG_LOG` | Write debug JSONL log (`1`/`true`) |
| `EMAIL_REPORT_LOGLEVEL` | `INFO` or `DEBUG` |
| `OLLAMA_URL` | Ollama API endpoint (default: `http://localhost:11434/api/generate`) |

### Known limitations

- LLM classifications are not reliable -- the model may miscategorize emails. Deterministic rules in code correct the most critical cases (SPAM/PHISHING priority, self-sent detection, addressing), but category assignment still depends on the LLM.
- Auto-triage uses a crash-safe copy-before-delete approach: the copy in the target folder is verified before the original is marked as deleted. With UIDPLUS (RFC 4315), only our specific UIDs are expunged; without UIDPLUS, originals are only marked `\Deleted` (no EXPUNGE) and cleaned up by the server/client later. Each run re-processes all emails in the date range so that priorities can be updated by re-running with a better model.
- The prompt and deterministic output fields (Summary, Context, Actions) are in German. The prompt instructions are in English.
- Tested primarily with Dovecot-based IMAP servers. Other servers may behave differently.

### Acknowledgements

Developed by human with AI assistance (e.g. Claude, Anthropic).

### License

[MIT](LICENSE)

---

## Deutsch

> **LLM-gestuetzte E-Mail-Triage** -- ein lokales Ollama-Modell liest die Inbox und verschickt einen priorisierten Tagesbericht.

### WARNUNG

**Dieses Projekt befindet sich in einem sehr fruehen Entwicklungsstadium (Alpha). Es ist NICHT produktionsreif.**

- Nur mit einem **dedizierten Test-E-Mail-Account** (Dummy-Account) verwenden.
- NICHT auf die echte Inbox anwenden, wenn verlorene oder verschobene E-Mails ein Problem waeren.
- Die Auto-Triage-Funktion kopiert E-Mails in IMAP-Ordner und setzt Flags (Stern, geloescht). Ein Crash-sicheres Design stellt sicher, dass keine E-Mail verloren geht (Kopie wird verifiziert bevor das Original zum Loeschen markiert wird), aber ein Fehler koennte E-Mails in unerwartete Ordner ablegen oder Flags aendern.
- LLM-Ausgaben sind nicht-deterministisch. Klassifikationen (SPAM, PHISHING, FYI, ACTIONABLE) koennen falsch sein.
- **Keine Garantie.** Siehe [LICENSE](LICENSE).

### Was es tut

inbox-sentinel verbindet sich mit einer IMAP-Mailbox, holt aktuelle E-Mails, gruppiert sie in Konversations-Threads und schickt jeden Thread an ein lokales LLM (via Ollama) zur Analyse. Das LLM klassifiziert jeden Thread nach Kategorie, Prioritaet und Adressierung. Die Ergebnisse werden in einen HTML-Report kompiliert und per E-Mail zurueckgeschickt. Optional erhalten alle E-Mails einen X-Priority-Header (sichtbar in Thunderbird, Outlook, Apple Mail), SPAM/PHISHING-E-Mails werden in Quarantaene-Ordner verschoben, hochprioritaere E-Mails mit Stern geflaggt, LLM-generierte Antwortentwuerfe im Drafts-Ordner abgelegt und eine Sender-Wissensbank aufgebaut, die kuenftige Analysen mit Kontext versorgt.

### Kernfunktionen

- **Lokales LLM** -- E-Mails verlassen nie den eigenen Rechner (Ollama laeuft lokal)
- **Threading** -- E-Mails werden zu Konversations-Threads gruppiert (Union-Find auf Header + Betreff-Fallback)
- **Prioritaet 1-5** -- dringende Eintraege erscheinen zuerst im Report
- **Kategorien** -- SPAM, PHISHING, FYI, ACTIONABLE mit deterministischen Post-Processing-Regeln im Code
- **Auto-Triage** -- crash-sichere IMAP-Nachbearbeitung: jede E-Mail erhaelt einen X-Priority-Header (1-5, sichtbar in den meisten Clients). SPAM/PHISHING in Quarantaene, hochprioritaere ACTIONABLE mit Stern. Nutzt verifiziertes Copy-before-Delete; erneuter Lauf mit besserem Modell ueberschreibt vorherige Prioritaeten
- **Auto-Draft** -- optionale LLM-generierte Antwortentwuerfe im IMAP-Drafts-Ordner (erkennt `\Drafts` Special-Use-Ordner per RFC 6154, `[Sentinel-Entwurf]`-Prefix im Betreff, opt-in)
- **Auto-Contacts** -- optionale Sender-Wissensbank aus E-Mails; liefert Kontext fuer Analyse- und Draft-Prompts (opt-in)
- **Profile** -- Server- und Account-Einstellungen als JSON-Profile speichern/laden
- **Plattformuebergreifend** -- Bootstrap-Skripte fuer Linux/macOS (sh) und Windows (PowerShell)

### Voraussetzungen

#### 1. Python 3.10+

Download: [python.org](https://www.python.org/downloads/). `python3` (bzw. `python`) und `pip` muessen im Terminal verfuegbar sein.

#### 2. Ollama (lokaler LLM-Server)

inbox-sentinel nutzt [Ollama](https://ollama.com/), um ein Sprachmodell lokal auf dem eigenen Rechner auszufuehren. Es werden keine Daten an externe Server gesendet.

**Ollama installieren:**
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **macOS:** Download von [ollama.com/download](https://ollama.com/download) oder `brew install ollama`
- **Windows:** Download von [ollama.com/download](https://ollama.com/download)

**Modell herunterladen:**

```bash
ollama pull gpt-os-20b
```

Das laedt ca. 8 GB herunter. Andere instruction-tuned Modelle funktionieren ebenfalls (z.B. `llama3.1:8b-instruct-q8_0`), aber der Prompt ist fuer `qwen2.5` optimiert. Ollama muss laufen bevor inbox-sentinel gestartet wird (die `run.sh`/`run.ps1`-Skripte versuchen es automatisch zu starten).

#### 3. Test-E-Mail-Account

Ein IMAP/SMTP-E-Mail-Account wird benoetigt. **Zum Testen einen dedizierten Dummy-Account verwenden** -- nicht die echte Inbox benutzen (siehe [Warnung oben](#warnung)).

### Installation

```bash
# 1. Klonen
git clone https://github.com/jfrochte/inbox-sentinel.git
cd inbox-sentinel

# 2. Bootstrap (erstellt venv + installiert Python-Abhaengigkeiten)
./bootstrap.sh        # Linux / macOS
# .\bootstrap.ps1    # Windows PowerShell
```

### Benutzung

```bash
# Option A: Run-Skript verwenden (prueft Ollama, aktiviert venv)
./run.sh              # Linux / macOS
# .\run.ps1          # Windows PowerShell

# Option B: Direkt ausfuehren (venv muss aktiv sein, Ollama muss laufen)
source .venv/bin/activate
python -m email_report
```

Der interaktive Prompt fuehrt durch Server-Einstellungen, Account-Daten und Profilverwaltung. Beim ersten Lauf werden alle Einstellungen abgefragt; bei weiteren Laeufen kann ein gespeichertes Profil geladen werden.

### Architektur

```
email_report/
  config.py        -- Config-Dataclass, Profile speichern/laden
  interactive.py   -- Alle User-Prompts (Profilwahl, Settings)
  imap_client.py   -- IMAP Fetch (read-only) und crash-sichere Auto-Triage
  email_parser.py  -- MIME-Decoding, Body-Extraktion
  threading.py     -- Thread-Gruppierung (Union-Find auf Header + Betreff-Fallback)
  llm.py           -- Ollama-Integration, Validation, Repair-Pass,
                      deterministische Addressing-Erkennung & Post-Processing
  report.py        -- Sortierung, HTML-Generierung, Block-Parsing
  smtp_client.py   -- HTML-Report per SMTP verschicken
  drafts.py        -- Auto-Draft: LLM-Antwortentwuerfe, IMAP APPEND
  contacts.py      -- Auto-Contacts: Sender-Profile als JSON, LLM-Extraktion
  main.py          -- Orchestrierung (Glue zwischen allen Modulen)
  utils.py         -- Logging, File-Helper

prompt.txt             -- LLM-Analyseprompt (Englisch, Ausgabe auf Deutsch)
draft_prompt.txt       -- LLM-Draft-Generierungsprompt
contact_prompt.txt     -- LLM-Kontaktextraktionsprompt
profiles/              -- Gespeicherte JSON-Profile (nicht in git)
contacts/              -- Sender-Profile als JSON (nicht in git)
```

### Verarbeitungspipeline

1. Profil laden oder interaktiv konfigurieren
2. IMAP: E-Mails holen (read-only, `BODY.PEEK[]`)
3. E-Mails in Konversations-Threads gruppieren (Union-Find)
4. Pro Thread: Sender-Kontakt laden -> LLM-Analyse -> validieren -> ggf. reparieren -> Fallback
5. Deterministisches Post-Processing (Adressierung, Prioritaets-Caps, SPAM/PHISHING Enforcement)
6. Pro Thread (optional): LLM-Antwortentwurf generieren, Sender-Kontakt aktualisieren
7. Nach Prioritaet sortieren, HTML-Report generieren
8. Report per SMTP verschicken
9. Drafts in IMAP-Drafts-Ordner speichern (erkennt `\Drafts` Special-Use automatisch, optional)
10. Auto-Triage: alle E-Mails erhalten X-Priority via FETCH + APPEND + verify + delete. SPAM/PHISHING → Quarantaene + `\Seen`, FYI → nur X-Priority, ACTIONABLE Prio 1-2 → `\Flagged`. Erneuter Lauf ueberschreibt Prioritaeten (optional)

### Konfiguration

Alle Einstellungen werden beim ersten Lauf interaktiv abgefragt. Profile werden als JSON unter `profiles/` gespeichert (Passwoerter werden nie gespeichert).

Wichtige Environment-Variablen:

| Variable | Beschreibung |
|---|---|
| `EMAIL_REPORT_DEBUG` | Temp-Dateien nach dem Lauf behalten (`1`/`true`) |
| `EMAIL_REPORT_DEBUG_LOG` | Debug JSONL Log schreiben (`1`/`true`) |
| `EMAIL_REPORT_LOGLEVEL` | `INFO` oder `DEBUG` |
| `OLLAMA_URL` | Ollama API Endpoint (Default: `http://localhost:11434/api/generate`) |

### Bekannte Einschraenkungen

- LLM-Klassifikationen sind nicht zuverlaessig -- das Modell kann E-Mails falsch einordnen. Deterministische Regeln im Code korrigieren die kritischsten Faelle (SPAM/PHISHING-Prioritaet, Self-Sent-Erkennung, Addressing), aber die Kategorie-Zuordnung haengt weiterhin vom LLM ab.
- Auto-Triage nutzt einen crash-sicheren Copy-before-Delete-Ansatz: die Kopie im Zielordner wird verifiziert bevor das Original als geloescht markiert wird. Mit UIDPLUS (RFC 4315) werden nur unsere spezifischen UIDs entfernt; ohne UIDPLUS werden Originale nur als `\Deleted` markiert (kein EXPUNGE) und spaeter vom Server/Client aufgeraeumt. Jeder Lauf verarbeitet alle E-Mails im Zeitraum neu, sodass Prioritaeten durch erneuten Lauf mit besserem Modell aktualisiert werden koennen.
- Der Prompt und die deterministischen Ausgabefelder (Summary, Context, Actions) sind auf Deutsch. Die Prompt-Anweisungen sind auf Englisch.
- Primaer mit Dovecot-basierten IMAP-Servern getestet. Andere Server koennten sich anders verhalten.

### Danksagung

Menschlicher Entwickler mit KI-Unterstuetzung (u.a. Claude, Anthropic).

### Lizenz

[MIT](LICENSE)
