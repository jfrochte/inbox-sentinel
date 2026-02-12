"""
report.py -- HTML generation (cards + pre), sorting, and block parsing.

Dependencies within the package:
  - utils (write_secure)

Provides _parse_llm_summary_block, used both here for HTML generation
and in llm.py for validation. Import direction: llm.py imports from
report.py (not reverse) to avoid circular imports.
"""

# ============================================================
# External dependencies
# ============================================================
import os
import re
import html

# ============================================================
# Internal package imports
# ============================================================
from email_report.utils import write_secure
from email_report.config import DEFAULT_SORT_FOLDERS
from email_report.i18n import t


# ============================================================
# Block separator (defined once, used everywhere).
# Intentionally not a plain dashes block so that email content
# (signatures, forwarding markers) never accidentally contains it.
# ============================================================
BLOCK_SEPARATOR = "====== BLOCK_SEP ======"


# ============================================================
# Priority extraction and sorting (deliberately tolerant)
# ============================================================
def extract_priority(text: str) -> int:
    """
    Tolerant extraction: looks for "Priority" anywhere and takes the first digit.
    Deliberately lenient to handle minor LLM output variations.
    """
    for line in (text or "").splitlines():
        if "Priority" in line or "Priorität" in line:
            m = re.search(r"(\d)", line)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 5:
                    return val
    return 5


def sort_summaries_by_priority(input_filename: str, output_filename: str) -> None:
    """
    Splits the text by separator and sorts blocks by priority (ascending).
    """
    with open(input_filename, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.split(BLOCK_SEPARATOR) if b.strip()]
    items = [(b, extract_priority(b)) for b in blocks]
    items.sort(key=lambda x: x[1])

    out_parts = []
    for idx, (block, _prio) in enumerate(items, start=1):
        out_parts.append(f"E-Mail Nummer: {idx}\n{block}\n{BLOCK_SEPARATOR}\n")

    write_secure(output_filename, "".join(out_parts))


# ============================================================
# LLM block parser
# ============================================================
def _parse_llm_summary_block(block: str) -> dict:
    """
    Parses an LLM block (the lines from prompt.txt) into a dict.
    Robust against missing fields and minor deviations in LLM output.

    Typical issues handled:
    - Multiple fields on a single line (e.g. "Sender: ... | Addressing: ... | Asked: NO")
    - Labels in German/English (Betreff/Subject, Von/From/Sender, ...)
    - Actions/Summary as sections with continuation lines (bullet points or multiple lines)
    """
    out = {
        "subject": "",
        "sender": "",
        "category": "ACTIONABLE",
        "context": "",
        "addressing": "UNKNOWN",
        "asked": "NO",
        "priority": 5,
        "status": "OK",
        "actions": "",
        "summary": "",
        "raw_excerpt": "",
        "thread_size": 1,
        "draft_status": "",
    }

    # Strip "E-Mail Nummer: X" lines (inserted by sort_summaries_by_priority)
    lines = [ln.rstrip("\n") for ln in (block or "").splitlines()]
    lines = [ln for ln in lines if not ln.strip().startswith("E-Mail Nummer:")]

    # Label synonyms (case-insensitive)
    sep = r"\s*[:=\-]\s*"  # LLM sometimes uses ':' or '-' or '='

    label_regexes = [
        ("subject", re.compile(rf"(?i)\b(subject|betreff){sep}")),
        ("sender", re.compile(rf"(?i)\b(sender|from|von){sep}")),
        ("category", re.compile(rf"(?i)\b(category|kategorie){sep}")),
        ("context", re.compile(rf"(?i)\b(context|kontext){sep}")),
        ("addressing", re.compile(rf"(?i)\b(addressing|adressierung|recipients|empf[aä]nger){sep}")),
        ("asked", re.compile(rf"(?i)\b(asked\s*directly|asked-directly|asked|direkt\s*angesprochen){sep}")),
        ("priority", re.compile(rf"(?i)\b(priority|priorit[aä]t|prio){sep}")),
        ("status", re.compile(rf"(?i)\b(llm\s*status|llm-status|status){sep}")),
        ("thread_size", re.compile(rf"(?i)\b(thread[\s-]?size|thread[\s-]?groesse){sep}")),
        ("draft_status", re.compile(rf"(?i)\b(draft[\s-]?status|draft|entwurf){sep}")),
        ("raw_excerpt", re.compile(rf"(?i)\b(raw\s*excerpt|raw-excerpt|excerpt|auszug){sep}")),
        ("summary", re.compile(rf"(?i)\b(summary|zusammenfassung){sep}")),
        # "Actions for <Person>" is common but variable
        ("actions", re.compile(rf"(?i)\bactions\s*for\s+[^:=\-]{{1,80}}{sep}")),
        ("actions", re.compile(rf"(?i)\b(actions|action\s*items|todo|to-do|aufgaben){sep}")),
    ]

    def set_value(key: str, val: str) -> None:
        v = (val or "").strip()
        if not v:
            return

        if key == "category":
            vv = v.strip().upper()
            if vv in ("SPAM", "PHISHING", "FYI", "ACTIONABLE"):
                out["category"] = vv
            else:
                out["category"] = "ACTIONABLE"
            return

        if key == "priority":
            m = re.search(r"(\d)", v)
            if m:
                try:
                    p = int(m.group(1))
                    out["priority"] = p if 1 <= p <= 5 else 5
                except Exception:
                    out["priority"] = 5
            return

        if key == "asked":
            # Normalise but do not destroy the original content.
            vv = v.strip().upper()
            if vv in ("YES", "Y", "JA", "J"):
                out["asked"] = "YES"
            elif vv in ("NO", "N", "NEIN"):
                out["asked"] = "NO"
            else:
                out["asked"] = v
            return

        if key == "status":
            out["status"] = v.strip().upper()
            return

        if key == "thread_size":
            m = re.search(r"(\d+)", v)
            if m:
                out["thread_size"] = int(m.group(1))
            return

        if key == "draft_status":
            out["draft_status"] = v
            return

        if key in ("actions", "summary", "context", "raw_excerpt"):
            if out[key]:
                out[key] = (out[key] + "\n" + v).strip()
            else:
                out[key] = v
            return

        out[key] = v

    current_section = None  # "actions" or "summary" or "context" or "raw_excerpt"

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Special case: section headers without a clear "Key: Value" (some models do this)
        if re.match(r"(?i)^actions\b\s*([\-=]\s*)?$", line):
            current_section = "actions"
            continue
        if re.match(r"(?i)^(summary|zusammenfassung)\b\s*([\-=]\s*)?$", line):
            current_section = "summary"
            continue
        if re.match(r"(?i)^(context|kontext)\b\s*([\-=]\s*)?$", line):
            current_section = "context"
            continue
        if re.match(r"(?i)^(raw\s*excerpt|raw-excerpt|excerpt|auszug)\b\s*([\-=]\s*)?$", line):
            current_section = "raw_excerpt"
            continue

        # Multiple fields on one line: find all label starts and extract values.
        hits = []
        for key, rx in label_regexes:
            for m in rx.finditer(line):
                hits.append((m.start(), m.end(), key))

        if hits:
            hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
            # Remove overlapping matches (longest match at each position wins)
            dedup = []
            for s, e, k in hits:
                # Check whether this match falls inside an already accepted one
                overlaps = False
                for ps, pe, _pk in dedup:
                    if ps <= s < pe:
                        overlaps = True
                        break
                if overlaps:
                    continue
                dedup.append((s, e, k))
            hits = sorted(dedup, key=lambda t: t[0])

            for i, (s, e, k) in enumerate(hits):
                end = hits[i + 1][0] if i + 1 < len(hits) else len(line)
                val = line[e:end].strip()
                # Strip only outer delimiters, not inner ones.
                val = val.strip(" |\t")
                set_value(k, val)
                current_section = k if k in ("actions", "summary") else None
            continue

        # Default case: a "Key: Value" line or a continuation line.
        m = re.match(r"^([^:=\-]{2,60})\s*[:=\-]\s*(.*)$", line)
        if m:
            key_raw = m.group(1).strip()
            val = m.group(2).strip()

            # Normalise key
            k = key_raw.lower().strip()
            if k in ("subject", "betreff"):
                set_value("subject", val)
                current_section = None
            elif k in ("sender", "from", "von"):
                set_value("sender", val)
                current_section = None
            elif k in ("category", "kategorie"):
                set_value("category", val)
                current_section = None
            elif k in ("context", "kontext"):
                set_value("context", val)
                current_section = "context"
            elif k in ("addressing", "adressierung", "recipients", "empfänger", "empfaenger"):
                set_value("addressing", val)
                current_section = None
            elif k in ("asked-directly", "asked directly", "asked", "direkt angesprochen"):
                set_value("asked", val)
                current_section = None
            elif k in ("priority", "priorität", "prioritaet", "prio"):
                set_value("priority", val)
                current_section = None
            elif k in ("llm-status", "llm status", "status"):
                set_value("status", val)
                current_section = None
            elif k in ("thread-size", "thread size", "thread-groesse", "thread groesse"):
                set_value("thread_size", val)
                current_section = None
            elif k in ("draft-status", "draft status", "draft", "entwurf"):
                set_value("draft_status", val)
                current_section = None
            elif k in ("raw-excerpt", "raw excerpt", "excerpt", "auszug"):
                set_value("raw_excerpt", val)
                current_section = "raw_excerpt"
            elif k.startswith("actions for") or k in ("actions", "action items", "todo", "to-do", "aufgaben"):
                set_value("actions", val)
                current_section = "actions"
            elif k in ("summary", "zusammenfassung"):
                set_value("summary", val)
                current_section = "summary"
            else:
                current_section = None
            continue

        # Continuation line (typical: Summary/Actions as bullet points or second line)
        if current_section in ("actions", "summary", "context", "raw_excerpt"):
            set_value(current_section, line)

    return out


def _split_actions(actions_raw: str) -> list:
    """Robust splitting of actions: ';' or newlines or bullet points."""
    txt = (actions_raw or "").strip()
    if not txt:
        return []

    low = txt.strip().lower()
    if low in ("keine", "keine.", "none", "none.", "n/a", "na"):
        return []

    parts = re.split(r";|\n", txt)
    out = []
    for p in parts:
        s = (p or "").strip()
        s = re.sub(r"^[\-\*•\u2022\u00b7]+\s*", "", s)
        if s and s.lower() not in ("keine", "keine.", "none", "none."):
            out.append(s)
    return out


# ============================================================
# HTML generation
# ============================================================
def summaries_to_html_pre(sorted_text: str) -> str:
    """
    Escapes against HTML injection and renders output inside a <pre> block.
    """
    escaped = html.escape(sorted_text)
    escaped = escaped.replace(BLOCK_SEPARATOR, "\n-----------------------\n")
    return (
        "<html><body>"
        "<p><b>Daily Email Report</b></p>"
        "<pre style=\"font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; white-space: pre-wrap;\">"
        + escaped +
        "</pre>"
        "</body></html>"
    )


def summaries_to_html_cards(sorted_text: str, title: str = "Daily Email Report", expected_count=None, auto_triage: bool = False, total_emails: int | None = None, draft_stats: dict | None = None) -> str:
    """
    Builds a more scannable HTML email (card view).
    Note: The "quick glance" section was intentionally removed.
    """
    blocks = [b.strip() for b in (sorted_text or "").split(BLOCK_SEPARATOR) if b.strip()]
    items = [_parse_llm_summary_block(b) for b in blocks]
    status_counts = {"OK": 0, "REPAIRED": 0, "FALLBACK": 0}
    for _it in items:
        st = str((_it.get("status") or "OK")).strip().upper()
        if st not in status_counts:
            st = "OK"
        status_counts[st] += 1

    def esc(x: str) -> str:
        return html.escape(x or "")

    def esc_ml(x: str) -> str:
        """Escape + convert newlines to <br> (for email HTML)."""
        return esc(x).replace("\n", "<br>")

    def prio_badge(priority: int) -> str:
        bg = {1: "#ffe5e5", 2: "#fff2dd", 3: "#fff9d6", 4: "#eef2ff", 5: "#f3f4f6"}.get(priority, "#f3f4f6")
        fg = {1: "#8a1f1f", 2: "#7a4a00", 3: "#6b5b00", 4: "#1f3a8a", 5: "#374151"}.get(priority, "#374151")
        return (
            f"<span style=\"display:inline-block;padding:2px 8px;border-radius:999px;"
            f"background:{bg};color:{fg};font-size:12px;font-weight:700;\">P{priority}</span>"
        )

    def category_badge(cat: str) -> str:
        cat = (cat or "ACTIONABLE").strip().upper()
        colors = {
            "SPAM": ("#ffe5e5", "#8a1f1f"),
            "PHISHING": ("#ffe5e5", "#8a1f1f"),
            "FYI": ("#dbeafe", "#1e40af"),
            "ACTIONABLE": ("#dcfce7", "#166534"),
        }
        bg, fg = colors.get(cat, ("#f3f4f6", "#374151"))
        return (
            f"<span style=\"display:inline-block;padding:2px 8px;border-radius:999px;"
            f"background:{bg};color:{fg};font-size:12px;font-weight:700;margin-left:4px;\">{esc(cat)}</span>"
        )

    parts = []
    parts.append("<html><body style=\"margin:0;padding:0;background:#f5f6f7;\">")
    parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\">")
    parts.append("<tr><td align=\"center\" style=\"padding:16px;\">")
    parts.append("<table role=\"presentation\" width=\"680\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:680px;width:100%;\">")

    # Header
    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
    parts.append(f"<div style=\"font-size:20px;font-weight:800;color:#111827;\">{esc(title)}</div>")
    if items:
        reported = len(items)
        if expected_count is None:
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">{esc(t('report.count_mails', count=reported))}</div>")
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">OK: {status_counts['OK']} | Repaired: {status_counts['REPAIRED']} | Fallback: {status_counts['FALLBACK']}</div>")
            if draft_stats:
                ds_gen = draft_stats.get("generated", 0)
                ds_fail = draft_stats.get("failed", 0)
                if ds_gen or ds_fail:
                    parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">{esc(t('report.drafts_stats', generated=ds_gen, failed=ds_fail))}</div>")
        else:
            if total_emails is not None and total_emails != expected_count:
                parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">{esc(t('report.fetched_threads_reported', total=total_emails, threads=expected_count, reported=reported))}</div>")
            else:
                parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">{esc(t('report.fetched_reported', fetched=expected_count, reported=reported))}</div>")
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">OK: {status_counts['OK']} | Repaired: {status_counts['REPAIRED']} | Fallback: {status_counts['FALLBACK']}</div>")
            if draft_stats:
                ds_gen = draft_stats.get("generated", 0)
                ds_fail = draft_stats.get("failed", 0)
                if ds_gen or ds_fail:
                    parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">{esc(t('report.drafts_stats', generated=ds_gen, failed=ds_fail))}</div>")
            if expected_count != reported:
                diff = expected_count - reported
                if diff > 0:
                    hint = t("report.threads_missing", diff=diff, expected=expected_count, reported=reported)
                else:
                    hint = t("report.threads_extra", diff=-diff, expected=expected_count, reported=reported)
                parts.append(f"<div style=\"margin-top:10px;padding:10px;border-radius:10px;border:1px solid #fecaca;background:#fff1f2;color:#991b1b;font-size:12px;\"><b>{esc(t('report.warning_label'))}</b> {esc(hint)}</div>")
    parts.append("</td></tr>")
    parts.append("<tr><td style=\"height:12px;\"></td></tr>")

    if not items:
        parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
        parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
                     "style=\"background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;\">")
        parts.append(f"<tr><td style=\"padding:14px; color:#374151;\">{esc(t('report.no_mails_found'))}</td></tr></table>")
        parts.append("</td></tr>")
        parts.append("</table></td></tr></table></body></html>")
        return "".join(parts)

    # Liste
    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
    parts.append(f"<div style=\"font-size:14px;font-weight:800;color:#111827;margin-bottom:8px;\">{esc(t('report.section_mails'))}</div>")
    parts.append("</td></tr>")

    for it in items:
        p = int(it.get("priority", 5) or 5)
        subj = esc(it.get("subject", ""))
        sender = esc(it.get("sender", ""))
        cat = (it.get("category") or "ACTIONABLE").strip().upper()
        addressing = esc(it.get("addressing", "UNKNOWN"))
        asked = esc(it.get("asked", "NO"))
        summary = esc_ml(it.get("summary", ""))
        actions_raw = (it.get("actions") or "").strip()
        actions = _split_actions(actions_raw)
        context = (it.get("context") or "").strip()
        status = (it.get("status") or "OK").strip().upper()
        raw_excerpt = (it.get("raw_excerpt") or "").strip()
        thread_size = int(it.get("thread_size", 1) or 1)

        parts.append(f"<tr class=\"email-card\" data-priority=\"{p}\" data-category=\"{cat}\"><td style=\"padding-bottom:10px;\">")
        parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
                     "style=\"background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;\">")
        parts.append("<tr><td style=\"padding:12px 14px;\">")

        parts.append("<div style=\"display:flex;align-items:center;gap:10px;\">")
        parts.append(prio_badge(p))
        parts.append(category_badge(cat))
        if thread_size > 1:
            parts.append(
                f"<span style=\"display:inline-block;padding:2px 8px;border-radius:999px;"
                f"background:#e0e7ff;color:#3730a3;font-size:12px;font-weight:700;margin-left:4px;\">"
                f"Thread ({thread_size})</span>"
            )
        parts.append(f"<div style=\"font-size:15px;font-weight:800;color:#111827;\">{subj}</div>")
        parts.append("</div>")

        # Meta line (deliberately larger for better readability)
        parts.append(
            f"<div style=\"margin-top:6px;font-size:14px;line-height:1.4;color:#6b7280;\">"
            f"<b>Sender</b>: {sender}<br>"
            f"<b>Addressing</b>: {addressing} | <b>Asked</b>: {asked}"
            + (f" | <b>Status</b>: {esc(status)}" if status and status != "OK" else "")
            + f"</div>"
        )

        # Triage hint: shows target folder when auto_triage is active and category gets moved
        if auto_triage and cat in DEFAULT_SORT_FOLDERS:
            target_folder = DEFAULT_SORT_FOLDERS[cat]
            parts.append(
                f"<div style=\"margin-top:4px;font-size:12px;color:#6b7280;font-style:italic;\">"
                f"{esc(t('report.moved_to', folder=target_folder))}"
                f"</div>"
            )

        # Draft hint
        draft_st = (it.get("draft_status") or "").strip().lower()
        if draft_st == "ok":
            parts.append(
                f"<div style=\"margin-top:4px;font-size:12px;color:#166534;font-style:italic;\">"
                f"{esc(t('report.draft_created'))}"
                f"</div>"
            )

        if summary:
            parts.append(f"<div style=\"margin-top:10px;color:#374151;line-height:1.45;\">{summary}</div>")

        if context:
            parts.append(f"<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">{esc(t('report.section_context'))}</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:10px;\">{esc_ml(context)}</div>")

        if actions and all(a.lower() not in ("keine.", "keine", "none", "none.") for a in actions):
            parts.append(f"<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">{esc(t('report.section_actions'))}</div>")
            parts.append("<ul style=\"margin:6px 0 0 18px;padding:0;color:#111827;\">")
            for a in actions:
                parts.append(f"<li style=\"margin:4px 0;line-height:1.35;\">{esc(a)}</li>")
            parts.append("</ul>")

        show_excerpt = bool(raw_excerpt) and (status in ("FALLBACK", "REPAIRED") or p in (1, 2))
        if show_excerpt:
            parts.append(f"<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">{esc(t('report.section_excerpt'))}</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;\">{esc_ml(raw_excerpt)}</div>")

        parts.append("</td></tr></table>")
        parts.append("</td></tr>")

    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;padding-top:8px;\">")
    parts.append(esc(t("report.footer_hint")))
    parts.append("</td></tr>")

    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


def summaries_to_html(sorted_text: str, title: str = "Daily Email Report", expected_count=None, auto_triage: bool = False, total_emails: int | None = None, draft_stats: dict | None = None) -> str:
    """
    Default: card view.
    Fallback: set ENV EMAIL_REPORT_HTML_PRE=1 for the legacy <pre> output.
    """
    use_pre = os.environ.get("EMAIL_REPORT_HTML_PRE", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_pre:
        return summaries_to_html_pre(sorted_text)
    return summaries_to_html_cards(sorted_text, title=title, expected_count=expected_count, auto_triage=auto_triage, total_emails=total_emails, draft_stats=draft_stats)
