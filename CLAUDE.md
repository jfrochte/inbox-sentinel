# inbox-sentinel — Project Rules

## Language & Output
- `prompt.txt` and `draft_prompt.txt` are written in English
- LLM output fields (Summary, Context, Actions) MUST be in German
- LLM must use full names (first + last), never first names only

## Privacy & Git
- NEVER put real person names in prompt files or committed code — use "Max Mustermann"
- `contacts/*.vcf` is personal data → in `.gitignore`, only `contacts/.gitkeep` tracked
- `profiles/*.json` is personal data → in `.gitignore`, not on GitHub

## Module Dependencies
- Import direction: `llm` imports from `report` AND `threading` (not reverse) to avoid circular imports
- `threading.py` is a leaf module (no package deps, only `re`)
- `drafts.py` is a leaf module (imports from `threading` + `utils` only)
- `vcard.py` is a leaf module (`utils` only)
- `contacts.py` imports from `utils` + `vcard` + `requests`

## Categories
- Valid categories: SPAM, PHISHING, FYI, ACTIONABLE (default)
- SPAM/PHISHING always get Priority 5 and Actions "Keine."
- Sort folders: SPAM → Spam, PHISHING → Quarantine
- FYI has no target folder, no flags changed (only X-Priority via triage)

## Pipeline Order
- report send → IMAP save drafts → auto-triage (report has priority)
- Auto-triage applies to ALL UIDs in a thread (same category)

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

## Environment
- Use `.venv` via `run.sh` / `bootstrap.sh` — has all deps including bs4
- `run.sh` handles venv activation + Ollama reachability check
