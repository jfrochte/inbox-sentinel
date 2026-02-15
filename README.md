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

- **Browser GUI** -- full web interface (FastAPI + Vue 3) for profile management, pipeline execution with live progress, report preview with category filters, contact management, and run summary statistics
- **Local LLM** -- your emails never leave your machine (Ollama runs locally)
- **Threading** -- emails are grouped into conversation threads (Union-Find on headers + subject fallback)
- **Priority 1-5** -- urgent items surface first in the report
- **Categories** -- SPAM, PHISHING, FYI, ACTIONABLE with deterministic post-processing rules enforced in code
- **Auto-triage** -- crash-safe IMAP post-processing: every email gets an X-Priority header injected (1-5, visible in most clients). SPAM/PHISHING moved to quarantine, high-priority ACTIONABLE starred. Uses verified copy-before-delete; re-running with a better model overwrites previous priorities
- **Auto-draft** -- optional LLM-generated reply drafts saved to your IMAP Drafts folder (auto-detects `\Drafts` special-use folder per RFC 6154, `[Sentinel-Entwurf]` subject prefix, opt-in)
- **Auto-contacts** -- optional sender knowledge base built from emails; provides context to analysis and draft prompts. Contacts can be auto-updated via IMAP + LLM with field-level accept/reject diff in the GUI (opt-in)
- **Profiles** -- save/load server and account settings as JSON profiles
- **i18n** -- GUI available in English and German, LLM output matches email language
- **Cross-platform** -- launcher scripts for Linux/macOS (sh) and Windows (PowerShell)

### Prerequisites

#### 1. Python 3.10+

Download: [python.org](https://www.python.org/downloads/). Make sure `python3` (or `python`) and `pip` are available in your terminal.

#### 2. Node.js 18+ (for GUI frontend)

Download: [nodejs.org](https://nodejs.org/). Required to build the Vue 3 frontend. The launcher script handles this automatically.

#### 3. Ollama (local LLM server)

inbox-sentinel uses [Ollama](https://ollama.com/) to run a language model locally on your machine. No data is sent to external servers.

**Install Ollama:**
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **macOS:** Download from [ollama.com/download](https://ollama.com/download) or `brew install ollama`
- **Windows:** Download from [ollama.com/download](https://ollama.com/download)

**Download a model:**

```bash
ollama pull gpt-oss:20b
```

This downloads ~13 GB. Smaller models can also be used (e.g. `llama3.1:8b-instruct-q8_0`). Ollama must be running before you start inbox-sentinel (the launcher scripts will try to start it automatically).

#### 4. A test email account

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
# Start the GUI (checks Ollama, builds frontend if needed, opens browser)
./run_gui.sh          # Linux / macOS
# .\run_gui.ps1      # Windows PowerShell

# Or run directly (venv must be active, Ollama must be running)
source .venv/bin/activate
python -m email_report
```

The GUI opens at `http://127.0.0.1:8741`. Create or select a profile, enter your IMAP password, choose a date range, and start the pipeline. Progress is shown live. After completion, a run summary shows email/thread/sender counts and category breakdown (with colored alerts for SPAM/PHISHING). The full HTML report is available in the Report view.

### Architecture

```
email_report/
  config.py        -- Config dataclass, profile save/load
  imap_client.py   -- IMAP fetch (read-only) and crash-safe auto-triage
  email_parser.py  -- MIME decoding, body extraction
  threading.py     -- Thread grouping (Union-Find on headers + subject fallback)
  llm.py           -- Ollama interaction, validation, repair pass,
                      deterministic addressing detection & post-processing,
                      shared HTTP session for connection reuse
  report.py        -- Sorting, HTML generation, block parsing
  smtp_client.py   -- Send HTML report via SMTP
  drafts.py        -- Auto-draft: LLM reply drafts, IMAP APPEND
  contacts.py      -- Auto-contacts: per-sender vCard profiles, LLM extraction
  main.py          -- Orchestration: two-pass pipeline (analysis, then drafts)
  utils.py         -- Logging, file helpers
  llm_profiles.py  -- LLM profile loading (extraction vs creative)
  i18n/            -- Backend i18n (en/de), set_language(), t()
    en.json
    de.json
  prompts/         -- LLM prompt templates
    prompt.txt         -- Analysis prompt
    draft_prompt.txt   -- Draft generation prompt
    contact_prompt.txt -- Contact extraction prompt
  data/            -- Static data files
    llm_profiles.json  -- LLM temperature/context profiles
    organizations.json -- Email provider presets (Gmail, Outlook, etc.)

gui/                   -- Browser GUI (FastAPI backend)
  server.py            -- FastAPI app, CORS, static mount
  service.py           -- Thin adapter over business-logic modules
  progress.py          -- Thread-safe in-memory job store
  models.py            -- Pydantic request/response models
  routes/              -- API routes (profiles, jobs, contacts, health, config)
  frontend/            -- Vue 3 SPA (TypeScript, Pinia, vue-i18n, Vite)
    src/
      views/           -- Dashboard, Profile, Contacts, Report
      components/      -- Health, RunPanel, ProgressBar, ProfileForm, ContactForm
      stores/          -- Pinia stores (app, profile, job, contacts)
      i18n/            -- GUI i18n strings (en.json, de.json)
      api/             -- API client + TypeScript types

profiles/              -- Saved JSON profiles (excluded from git)
contacts/              -- Per-sender vCard files (excluded from git)
```

### Processing pipeline

1. Configure profile via GUI (server, account, features, LLM model)
2. IMAP: fetch emails for date range (read-only, `BODY.PEEK[]`)
3. Group emails into conversation threads (Union-Find)
4. **Pass 1 -- Analysis** (prompt.txt KV cache stays warm across all threads):
   - Per thread: load sender contact -> LLM analysis -> validate -> repair if needed -> fallback
   - Deterministic post-processing (addressing, priority caps, SPAM/PHISHING enforcement)
   - Prepare triage actions
5. **Pass 2 -- Drafts** (draft_prompt.txt KV cache stays warm, optional):
   - Per eligible thread: generate LLM reply draft
6. Sort results by priority, generate HTML report
7. Send report via SMTP
8. Save drafts to IMAP Drafts folder (auto-detects `\Drafts` special-use, optional)
9. Auto-triage: all emails get X-Priority injected via FETCH + APPEND + verify + delete. SPAM/PHISHING to quarantine + `\Seen`, FYI X-Priority only, ACTIONABLE prio 1-2 `\Flagged`. Re-running overwrites priorities (optional)
10. Dashboard shows run summary: emails, threads, unique senders, drafts, category breakdown

### Configuration

All settings are managed through the browser GUI. Profiles are saved to `profiles/` as JSON (passwords are never stored -- entered per session). Organization presets (Gmail, Outlook, etc.) pre-fill server settings.

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
- **Attachments are not analyzed.** Only the text body of emails is sent to the LLM. File attachments (PDFs, images, spreadsheets, etc.) are ignored. Important information hidden in attachments will not be reflected in the summary, priority, or category.
- LLM output fields (Summary, Context, Actions) match the email's language (auto-detected). The prompt instructions are in English.
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

- **Browser-GUI** -- vollstaendige Web-Oberflaeche (FastAPI + Vue 3) fuer Profilverwaltung, Pipeline-Ausfuehrung mit Live-Fortschritt, Report-Vorschau mit Kategorie-Filtern, Kontaktverwaltung und Lauf-Statistiken
- **Lokales LLM** -- E-Mails verlassen nie den eigenen Rechner (Ollama laeuft lokal)
- **Threading** -- E-Mails werden zu Konversations-Threads gruppiert (Union-Find auf Header + Betreff-Fallback)
- **Prioritaet 1-5** -- dringende Eintraege erscheinen zuerst im Report
- **Kategorien** -- SPAM, PHISHING, FYI, ACTIONABLE mit deterministischen Post-Processing-Regeln im Code
- **Auto-Triage** -- crash-sichere IMAP-Nachbearbeitung: jede E-Mail erhaelt einen X-Priority-Header (1-5, sichtbar in den meisten Clients). SPAM/PHISHING in Quarantaene, hochprioritaere ACTIONABLE mit Stern. Nutzt verifiziertes Copy-before-Delete; erneuter Lauf mit besserem Modell ueberschreibt vorherige Prioritaeten
- **Auto-Draft** -- optionale LLM-generierte Antwortentwuerfe im IMAP-Drafts-Ordner (erkennt `\Drafts` Special-Use-Ordner per RFC 6154, `[Sentinel-Entwurf]`-Prefix im Betreff, opt-in)
- **Auto-Contacts** -- optionale Sender-Wissensbank aus E-Mails; liefert Kontext fuer Analyse- und Draft-Prompts. Kontakte koennen per IMAP + LLM aktualisiert werden, mit feldweiser Uebernahme/Ablehnung in der GUI (opt-in)
- **Profile** -- Server- und Account-Einstellungen als JSON-Profile speichern/laden
- **i18n** -- GUI verfuegbar auf Englisch und Deutsch, LLM-Ausgabe passt sich der E-Mail-Sprache an
- **Plattformuebergreifend** -- Launcher-Skripte fuer Linux/macOS (sh) und Windows (PowerShell)

### Voraussetzungen

#### 1. Python 3.10+

Download: [python.org](https://www.python.org/downloads/). `python3` (bzw. `python`) und `pip` muessen im Terminal verfuegbar sein.

#### 2. Node.js 18+ (fuer GUI-Frontend)

Download: [nodejs.org](https://nodejs.org/). Wird zum Bauen des Vue-3-Frontends benoetigt. Das Launcher-Skript erledigt das automatisch.

#### 3. Ollama (lokaler LLM-Server)

inbox-sentinel nutzt [Ollama](https://ollama.com/), um ein Sprachmodell lokal auf dem eigenen Rechner auszufuehren. Es werden keine Daten an externe Server gesendet.

**Ollama installieren:**
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **macOS:** Download von [ollama.com/download](https://ollama.com/download) oder `brew install ollama`
- **Windows:** Download von [ollama.com/download](https://ollama.com/download)

**Modell herunterladen:**

```bash
ollama pull gpt-oss:20b
```

Das laedt ca. 13 GB herunter. Kleinere Modelle koennen ebenfalls genutzt werden (z.B. `llama3.1:8b-instruct-q8_0`). Ollama muss laufen bevor inbox-sentinel gestartet wird (die Launcher-Skripte versuchen es automatisch zu starten).

#### 4. Test-E-Mail-Account

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
# GUI starten (prueft Ollama, baut Frontend falls noetig, oeffnet Browser)
./run_gui.sh          # Linux / macOS
# .\run_gui.ps1      # Windows PowerShell

# Oder direkt ausfuehren (venv muss aktiv sein, Ollama muss laufen)
source .venv/bin/activate
python -m email_report
```

Die GUI oeffnet sich unter `http://127.0.0.1:8741`. Profil erstellen oder auswaehlen, IMAP-Passwort eingeben, Zeitraum waehlen und Pipeline starten. Der Fortschritt wird live angezeigt. Nach Abschluss zeigt eine Zusammenfassung E-Mail-/Thread-/Absender-Zahlen und Kategorie-Verteilung (mit farbigen Warnungen bei SPAM/Phishing). Der vollstaendige HTML-Report ist in der Report-Ansicht verfuegbar.

### Architektur

```
email_report/
  config.py        -- Config-Dataclass, Profile speichern/laden
  imap_client.py   -- IMAP Fetch (read-only) und crash-sichere Auto-Triage
  email_parser.py  -- MIME-Decoding, Body-Extraktion
  threading.py     -- Thread-Gruppierung (Union-Find auf Header + Betreff-Fallback)
  llm.py           -- Ollama-Integration, Validation, Repair-Pass,
                      deterministische Addressing-Erkennung & Post-Processing,
                      gemeinsame HTTP-Session fuer Connection-Reuse
  report.py        -- Sortierung, HTML-Generierung, Block-Parsing
  smtp_client.py   -- HTML-Report per SMTP verschicken
  drafts.py        -- Auto-Draft: LLM-Antwortentwuerfe, IMAP APPEND
  contacts.py      -- Auto-Contacts: Sender-vCards, LLM-Extraktion
  main.py          -- Orchestrierung: Zwei-Pass-Pipeline (Analyse, dann Drafts)
  utils.py         -- Logging, File-Helper
  llm_profiles.py  -- LLM-Profil-Laden (Extraction vs Creative)
  i18n/            -- Backend i18n (en/de), set_language(), t()
    en.json
    de.json
  prompts/         -- LLM-Prompt-Vorlagen
    prompt.txt         -- Analyseprompt
    draft_prompt.txt   -- Draft-Generierungsprompt
    contact_prompt.txt -- Kontaktextraktionsprompt
  data/            -- Statische Datendateien
    llm_profiles.json  -- LLM-Temperatur-/Kontext-Profile
    organizations.json -- E-Mail-Provider-Presets (Gmail, Outlook, etc.)

gui/                   -- Browser-GUI (FastAPI Backend)
  server.py            -- FastAPI App, CORS, Static Mount
  service.py           -- Duenner Adapter ueber Businesslogik-Module
  progress.py          -- Thread-sicherer In-Memory Job Store
  models.py            -- Pydantic Request/Response Models
  routes/              -- API-Routen (Profiles, Jobs, Contacts, Health, Config)
  frontend/            -- Vue 3 SPA (TypeScript, Pinia, vue-i18n, Vite)
    src/
      views/           -- Dashboard, Profile, Contacts, Report
      components/      -- Health, RunPanel, ProgressBar, ProfileForm, ContactForm
      stores/          -- Pinia Stores (app, profile, job, contacts)
      i18n/            -- GUI i18n Strings (en.json, de.json)
      api/             -- API Client + TypeScript Types

profiles/              -- Gespeicherte JSON-Profile (nicht in git)
contacts/              -- Sender-vCards (nicht in git)
```

### Verarbeitungspipeline

1. Profil ueber GUI konfigurieren (Server, Konto, Features, LLM-Modell)
2. IMAP: E-Mails fuer Zeitraum holen (read-only, `BODY.PEEK[]`)
3. E-Mails in Konversations-Threads gruppieren (Union-Find)
4. **Pass 1 -- Analyse** (prompt.txt KV-Cache bleibt warm ueber alle Threads):
   - Pro Thread: Sender-Kontakt laden -> LLM-Analyse -> validieren -> ggf. reparieren -> Fallback
   - Deterministisches Post-Processing (Adressierung, Prioritaets-Caps, SPAM/PHISHING Enforcement)
   - Triage-Aktionen vorbereiten
5. **Pass 2 -- Drafts** (draft_prompt.txt KV-Cache bleibt warm, optional):
   - Pro berechtigtem Thread: LLM-Antwortentwurf generieren
6. Nach Prioritaet sortieren, HTML-Report generieren
7. Report per SMTP verschicken
8. Drafts in IMAP-Drafts-Ordner speichern (erkennt `\Drafts` Special-Use automatisch, optional)
9. Auto-Triage: alle E-Mails erhalten X-Priority via FETCH + APPEND + verify + delete. SPAM/PHISHING in Quarantaene + `\Seen`, FYI nur X-Priority, ACTIONABLE Prio 1-2 `\Flagged`. Erneuter Lauf ueberschreibt Prioritaeten (optional)
10. Dashboard zeigt Lauf-Zusammenfassung: E-Mails, Threads, Absender, Drafts, Kategorie-Verteilung

### Konfiguration

Alle Einstellungen werden ueber die Browser-GUI verwaltet. Profile werden als JSON unter `profiles/` gespeichert (Passwoerter werden nie gespeichert -- Eingabe pro Sitzung). Organisations-Presets (Gmail, Outlook, etc.) fuellen Server-Einstellungen automatisch aus.

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
- **Anhaenge werden nicht analysiert.** Nur der Textkoerper der E-Mails wird an das LLM gesendet. Datei-Anhaenge (PDFs, Bilder, Tabellen etc.) werden ignoriert. Wichtige Informationen in Anhaengen fliessen nicht in Zusammenfassung, Prioritaet oder Kategorie ein.
- LLM-Ausgabefelder (Summary, Context, Actions) passen sich der E-Mail-Sprache an (automatische Erkennung). Die Prompt-Anweisungen sind auf Englisch.
- Primaer mit Dovecot-basierten IMAP-Servern getestet. Andere Server koennten sich anders verhalten.

### Danksagung

Menschlicher Entwickler mit KI-Unterstuetzung (u.a. Claude, Anthropic).

### Lizenz

[MIT](LICENSE)
