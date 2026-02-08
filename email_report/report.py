"""
report.py – HTML-Generierung (Karten + Pre), Sortierung und Block-Parsing.

Abhaengigkeiten innerhalb des Pakets:
  - utils (write_secure)

Dieses Modul stellt _parse_llm_summary_block bereit, das sowohl hier fuer die
HTML-Erzeugung als auch in llm.py fuer die Validierung genutzt wird.
Die Importrichtung ist: llm.py importiert aus report.py (nicht umgekehrt),
um zirkulaere Imports zu vermeiden.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import os
import re
import html

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import write_secure
from email_report.config import DEFAULT_SORT_FOLDERS


# ============================================================
# Prioritaet extrahieren und sortieren
# (bleibt bewusst tolerant)
# ============================================================
def extract_priority(text: str) -> int:
    """
    Tolerant: sucht irgendwo "Priority" und nimmt erste Ziffer.
    Das ist ok, weil du gesagt hast, es darf etwas "unsauber" sein.
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
    Splittet nach Trenner und sortiert nach Priority aufsteigend.
    """
    with open(input_filename, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.split("-----------------------") if b.strip()]
    items = [(b, extract_priority(b)) for b in blocks]
    items.sort(key=lambda x: x[1])

    out_parts = []
    for idx, (block, _prio) in enumerate(items, start=1):
        out_parts.append(f"E-Mail Nummer: {idx}\n{block}\n-----------------------\n")

    write_secure(output_filename, "".join(out_parts))


# ============================================================
# LLM-Block Parser
# ============================================================
def _parse_llm_summary_block(block: str) -> dict:
    """
    Parst einen LLM-Block (die Zeilen aus prompt.txt) in ein Dict.
    Robust gegen fehlende Felder und kleine Abweichungen im LLM-Output.

    Typische Probleme, die wir abfangen:
    - Mehrere Felder in einer Zeile (z.B. "Sender: ... | Addressing: ... | Asked: NO")
    - Labels auf Deutsch/Englisch (Betreff/Subject, Von/From/Sender, ...)
    - Actions/Summary als Abschnitt mit Folgelinien (Bulletpoints oder mehrere Zeilen)
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

    # "E-Mail Nummer: X" rauswerfen (kommt aus sort_summaries_by_priority)
    lines = [ln.rstrip("\n") for ln in (block or "").splitlines()]
    lines = [ln for ln in lines if not ln.strip().startswith("E-Mail Nummer:")]

    # Label-Synonyme (case-insensitive)
    sep = r"\s*[:=\-]\s*"  # LLM macht manchmal ':' oder '-' oder '='

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
        # Actions for <Person> ist haeufig, aber variabel
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
            # Normalisieren, aber Originalinhalt nicht kaputt machen.
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

    current_section = None  # "actions" oder "summary" oder "context" oder "raw_excerpt"

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Sonderfall: Abschnittsheader ohne klaren "Key: Value" (manche Modelle machen das)
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

        # Mehrere Felder in einer Zeile: wir suchen alle Label-Starts und schneiden Values.
        hits = []
        for key, rx in label_regexes:
            for m in rx.finditer(line):
                hits.append((m.start(), m.end(), key))

        if hits:
            hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
            # Ueberlappende Matches entfernen (laengster Match an jedem Punkt gewinnt)
            dedup = []
            for s, e, k in hits:
                # Pruefen ob dieser Match innerhalb eines bereits akzeptierten liegt
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
                # Entferne nur aeusserliche Trenner, nicht die innenliegenden.
                val = val.strip(" |\t")
                set_value(k, val)
                current_section = k if k in ("actions", "summary") else None
            continue

        # Standardfall: eine Zeile "Key: Value" oder eine Fortsetzungszeile.
        m = re.match(r"^([^:=\-]{2,60})\s*[:=\-]\s*(.*)$", line)
        if m:
            key_raw = m.group(1).strip()
            val = m.group(2).strip()

            # Key normalisieren
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

        # Fortsetzungszeile (typisch: Summary/Actions als Bulletpoints oder zweite Zeile)
        if current_section in ("actions", "summary", "context", "raw_excerpt"):
            set_value(current_section, line)

    return out


def _split_actions(actions_raw: str) -> list:
    """Robustes Splitten von Actions: ';' oder neue Zeilen oder Bulletpoints."""
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
# HTML Erstellung
# ============================================================
def summaries_to_html_pre(sorted_text: str) -> str:
    """
    Escape gegen HTML Injection und Ausgabe im <pre>.
    """
    escaped = html.escape(sorted_text)
    escaped = escaped.replace("-----------------------", "\n-----------------------\n")
    return (
        "<html><body>"
        "<p><b>Daily Email Report</b></p>"
        "<pre style=\"font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; white-space: pre-wrap;\">"
        + escaped +
        "</pre>"
        "</body></html>"
    )


def summaries_to_html_cards(sorted_text: str, title: str = "Daily Email Report", expected_count=None, auto_sort: bool = False, total_emails: int | None = None, draft_stats: dict | None = None) -> str:
    """
    Baut eine besser scanbare HTML-Mail (Kartenansicht).
    Hinweis: Der "Schnellblick" wurde bewusst entfernt.
    """
    blocks = [b.strip() for b in (sorted_text or "").split("-----------------------") if b.strip()]
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
        """Escapen + Zeilenumbrueche in <br> wandeln (fuers Mail-HTML)."""
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
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">Anzahl Mails: {reported}</div>")
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">OK: {status_counts['OK']} | Repaired: {status_counts['REPAIRED']} | Fallback: {status_counts['FALLBACK']}</div>")
            if draft_stats:
                ds_gen = draft_stats.get("generated", 0)
                ds_fail = draft_stats.get("failed", 0)
                if ds_gen or ds_fail:
                    parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">Drafts: {ds_gen} erstellt, {ds_fail} fehlgeschlagen</div>")
        else:
            if total_emails is not None and total_emails != expected_count:
                parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">Abgerufen: {total_emails} | Threads: {expected_count} | Im Report: {reported}</div>")
            else:
                parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:4px;\">Abgerufen: {expected_count} | Im Report: {reported}</div>")
            parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">OK: {status_counts['OK']} | Repaired: {status_counts['REPAIRED']} | Fallback: {status_counts['FALLBACK']}</div>")
            if draft_stats:
                ds_gen = draft_stats.get("generated", 0)
                ds_fail = draft_stats.get("failed", 0)
                if ds_gen or ds_fail:
                    parts.append(f"<div style=\"font-size:12px;color:#6b7280;margin-top:2px;\">Drafts: {ds_gen} erstellt, {ds_fail} fehlgeschlagen</div>")
            if expected_count != reported:
                parts.append("<div style=\"margin-top:10px;padding:10px;border-radius:10px;border:1px solid #fecaca;background:#fff1f2;color:#991b1b;font-size:12px;\"><b>WARNUNG:</b> Es fehlen Eintraege im Report. Bitte Log/Raw ansehen.</div>")
    parts.append("</td></tr>")
    parts.append("<tr><td style=\"height:12px;\"></td></tr>")

    if not items:
        parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
        parts.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
                     "style=\"background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;\">")
        parts.append("<tr><td style=\"padding:14px; color:#374151;\">Keine passenden Mails gefunden.</td></tr></table>")
        parts.append("</td></tr>")
        parts.append("</table></td></tr></table></body></html>")
        return "".join(parts)

    # Liste
    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;\">")
    parts.append("<div style=\"font-size:14px;font-weight:800;color:#111827;margin-bottom:8px;\">Mails</div>")
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

        parts.append("<tr><td style=\"padding-bottom:10px;\">")
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

        # Meta-Zeile (bewusst groesser, damit besser lesbar)
        parts.append(
            f"<div style=\"margin-top:6px;font-size:14px;line-height:1.4;color:#6b7280;\">"
            f"<b>Sender</b>: {sender}<br>"
            f"<b>Addressing</b>: {addressing} | <b>Asked</b>: {asked}"
            + (f" | <b>Status</b>: {esc(status)}" if status and status != "OK" else "")
            + f"</div>"
        )

        # Sortierhinweis: zeigt Zielordner wenn auto_sort aktiv und Kategorie verschoben wird
        if auto_sort and cat in DEFAULT_SORT_FOLDERS:
            target_folder = DEFAULT_SORT_FOLDERS[cat]
            parts.append(
                f"<div style=\"margin-top:4px;font-size:12px;color:#6b7280;font-style:italic;\">"
                f"Verschoben nach: {esc(target_folder)}"
                f"</div>"
            )

        # Draft-Hinweis
        draft_st = (it.get("draft_status") or "").strip().lower()
        if draft_st == "erstellt":
            parts.append(
                f"<div style=\"margin-top:4px;font-size:12px;color:#166534;font-style:italic;\">"
                f"Entwurf erstellt (Drafts)"
                f"</div>"
            )

        if summary:
            parts.append(f"<div style=\"margin-top:10px;color:#374151;line-height:1.45;\">{summary}</div>")

        if context:
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Kontext</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:10px;\">{esc_ml(context)}</div>")

        if actions and all(a.lower() not in ("keine.", "keine", "none", "none.") for a in actions):
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Actions</div>")
            parts.append("<ul style=\"margin:6px 0 0 18px;padding:0;color:#111827;\">")
            for a in actions:
                parts.append(f"<li style=\"margin:4px 0;line-height:1.35;\">{esc(a)}</li>")
            parts.append("</ul>")

        show_excerpt = bool(raw_excerpt) and (status in ("FALLBACK", "REPAIRED") or p in (1, 2))
        if show_excerpt:
            parts.append("<div style=\"margin-top:10px;font-size:12px;font-weight:800;color:#111827;\">Excerpt</div>")
            parts.append(f"<div style=\"margin-top:6px;color:#374151;line-height:1.45;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;\">{esc_ml(raw_excerpt)}</div>")

        parts.append("</td></tr></table>")
        parts.append("</td></tr>")

    parts.append("<tr><td style=\"font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#9ca3af;padding-top:8px;\">")
    parts.append("Hinweis: Inhalt wurde automatisch zusammengefasst. Bitte bei Unklarheiten die Original-Mail oeffnen.")
    parts.append("</td></tr>")

    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


def summaries_to_html(sorted_text: str, title: str = "Daily Email Report", expected_count=None, auto_sort: bool = False, total_emails: int | None = None, draft_stats: dict | None = None) -> str:
    """
    Default: Kartenansicht.
    Fallback: setze ENV EMAIL_REPORT_HTML_PRE=1 fuer den alten <pre>-Output.
    """
    use_pre = os.environ.get("EMAIL_REPORT_HTML_PRE", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_pre:
        return summaries_to_html_pre(sorted_text)
    return summaries_to_html_cards(sorted_text, title=title, expected_count=expected_count, auto_sort=auto_sort, total_emails=total_emails, draft_stats=draft_stats)
