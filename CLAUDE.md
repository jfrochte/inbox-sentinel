# inbox-sentinel — Project Rules

## Language & Output
- `prompt.txt` and `draft_prompt.txt` are written in English
- LLM output fields (Summary, Context, Actions) match the email's language (auto-detect via prompt)
- LLM must use full names (first + last), never first names only
- `language` config field controls UI language (default: `"en"`). No language gets special status.

## Privacy & Git
- NEVER put real person names in prompt files or committed code — use "Max Mustermann"
- `contacts/*.vcf` is personal data → in `.gitignore`, only `contacts/.gitkeep` tracked
- `profiles/*.json` is personal data → in `.gitignore`, not on GitHub

## Module Dependencies
- Import direction: `llm` imports from `report` AND `threading` (not reverse) to avoid circular imports
- `threading.py` is a leaf module (no package deps, only `re`)
- `drafts.py` imports from `threading` + `utils` + `llm_profiles` + `llm` (for `_session`)
- `vcard.py` is a leaf module (`utils` only)
- `contacts.py` imports from `utils` + `vcard` + `llm_profiles` + `llm` (for `_session`)
- `i18n/` is a leaf package (no internal deps, only `json` + `os`)
- `llm_profiles.py` is a leaf module (no package deps, only `json` + `os`)
- `gui/` package: `server.py` mounts routes, `service.py` adapts business-logic modules, `progress.py` is thread-safe job store

## Categories
- Valid categories: SPAM, PHISHING, FYI, ACTIONABLE (default)
- SPAM/PHISHING always get Priority 5 and Actions "Keine."
- Sort folders: SPAM → Spam, PHISHING → Quarantine
- FYI has no target folder, no flags changed (only X-Priority via triage)

## Pipeline Order
- Two-pass: Pass 1 = all analyses (prompt.txt KV cache warm), Pass 2 = all drafts (draft_prompt.txt KV cache warm)
- After both passes: report send → IMAP save drafts → auto-triage (report has priority)
- Auto-triage applies to ALL UIDs in a thread (same category)

## LLM Optimization
- Shared `requests.Session` (`_session` in `llm.py`) reused by `llm.py`, `drafts.py`, `contacts.py`
- Prompt order: fixed `prompt_base` first, then variable `user_context` + `sender_context` + email text (maximises Ollama KV cache prefix reuse)
- Two-pass pipeline keeps same prompt prefix warm across consecutive calls

## IMAP Safety
- Always use `readonly=True` + `BODY.PEEK[]` — never set Seen flag during fetch
- UID-based operations only (stable across sessions)
- Without UIDPLUS: only `STORE \Deleted` (no EXPUNGE)

## Auto-Draft Rules
- Skip conditions: SPAM/PHISHING/FYI, self-sent (newest mail), Actions "Keine." or empty
- `[Sentinel-Entwurf]` subject prefix to distinguish LLM drafts from user drafts
- Use quoted-printable encoding (not base64) for Thunderbird editability

## Auto-Contacts Rules
- One `.vcf` file per email address (vCard 3.0)
- NOTE field: LLM-section completely replaced on update, User-section (after `---\nUser:`) preserved
- Two separate IMAP searches (FROM + TO) instead of OR query (server compatibility)

## GUI
- FastAPI backend (`gui/`) + Vue 3 SPA (`gui/frontend/`)
- `python -m email_report` or `run_gui.sh` starts the server at `http://127.0.0.1:8741`
- Jobs run in background threads with progress polling (1s interval)
- Password: sent with job request, never persisted
- Frontend i18n: `gui/frontend/src/i18n/{en,de}.json` (separate from backend `email_report/i18n/`)
- Run summary stats: total_emails, thread_count, unique_senders, categories, draft_stats, triage_stats

## Environment
- Use `.venv` via `run.sh` / `bootstrap.sh` — has all deps including bs4, fastapi, uvicorn
- `run_gui.sh` handles venv activation + Ollama check + frontend build + browser open
- Node.js 18+ required for frontend build (Vite 5)
