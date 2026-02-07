"""
llm.py – Ollama-Interaktion, Analyse, Repair-Pass und Validierung.

Abhaengigkeiten innerhalb des Pakets:
  - utils (_tail_text fuer den Repair-Pass, log)
  - report (_parse_llm_summary_block fuer Validierung)

Importrichtung: llm -> report (nicht umgekehrt), damit keine zirkulaeren
Imports entstehen.
"""

# ============================================================
# Externe Abhaengigkeiten
# ============================================================
import re

import requests

# ============================================================
# Interne Paket-Imports
# ============================================================
from email_report.utils import log, _tail_text
from email_report.report import _parse_llm_summary_block


# ============================================================
# Erlaubte Werte fuer Validierung
# ============================================================
ALLOWED_ADDRESSING = {"DIRECT", "CC", "GROUP", "LIST", "UNKNOWN"}
ALLOWED_ASKED = {"YES", "NO"}
ALLOWED_STATUS = {"OK", "REPAIRED", "FALLBACK"}


# ============================================================
# LLM-Antwort Extraktion
# ============================================================
def _extract_llm_text_from_json(data):
    """
    Extrahiert den eigentlichen Text aus unterschiedlichen API-Formaten.
    Unterstuetzt:
    - Ollama /api/chat: {"message": {"content": "..."}}
    - OpenAI-kompatibel: {"choices":[{"message":{"content":"..."}}]} oder {"choices":[{"text":"..."}]}
    - Ollama /api/generate: {"response": "..."} (manche Modelle liefern zusaetzlich "thinking")
    Gibt (text, source) zurueck.
    """
    if not isinstance(data, dict):
        return "", "non_dict"

    # 1) Chat-Antwort (Ollama /api/chat)
    msg = data.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str) and (msg.get("content") or "").strip():
        return msg.get("content") or "", "message.content"

    # 2) OpenAI-kompatibel
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            m = ch0.get("message")
            if isinstance(m, dict) and isinstance(m.get("content"), str) and (m.get("content") or "").strip():
                return m.get("content") or "", "choices[0].message.content"
            if isinstance(ch0.get("text"), str) and (ch0.get("text") or "").strip():
                return ch0.get("text") or "", "choices[0].text"

    # 3) Generate-Antwort (Ollama /api/generate)
    resp = data.get("response")
    if isinstance(resp, str) and resp.strip():
        return resp, "response"

    # 4) Manche Server nutzen andere Keys
    for k in ("output", "content", "text"):
        if isinstance(data.get(k), str) and (data.get(k) or "").strip():
            return data.get(k) or "", k

    # 5) Spezialfall: Modelle mit "thinking" liefern dort manchmal den gesamten Text,
    # waehrend "response" leer bleibt. Wir nutzen das nur als Fallback, damit
    # der Repair-Pass daraus wieder ein sauberes Format machen kann.
    thinking = data.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        return thinking, "thinking"

    return "", "none"


# ============================================================
# LLM Analyse via Ollama
# ============================================================
def analyze_email_via_ollama(model: str, email_text: str, person: str, ollama_url: str, prompt_base: str, debug: dict | None = None) -> str:
    headers = {"Content-Type": "application/json"}

    # Platzhalter ersetzen (falls im prompt.txt vorhanden)
    base = prompt_base.format(person=person)

    prompt = (
        base
        + "\n--- EMAIL START ---\n"
        + email_text
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 32768,
            "num_predict": 4000,
        },
    }

    try:
        resp = requests.post(ollama_url, json=payload, headers=headers, timeout=180)

        if debug is not None:
            debug["http_status"] = resp.status_code
            debug["resp_text_len"] = len(resp.text or "")
            debug["resp_text_head"] = (resp.text or "")[:800]

        if resp.status_code != 200:
            return (
                "Subject: (Error)\n"
                "Sender: (Error)\n"
                f"Summary: HTTP error {resp.status_code}: {resp.text}\n"
                "Priority: 5\n"
                f"Actions for {person}: None\n"
            )

        try:
            data = resp.json()
        except Exception as je:
            if debug is not None:
                debug["json_error"] = str(je)
            return ""

        if debug is not None:
            debug["json_keys"] = sorted([str(k) for k in data.keys()])
            if "done_reason" in data:
                debug["done_reason"] = str(data.get("done_reason"))
            if "model" in data:
                debug["server_model"] = str(data.get("model"))
            if "thinking" in data and isinstance(data.get("thinking"), str):
                debug["thinking_len"] = len(data.get("thinking") or "")

        if isinstance(data, dict) and data.get("error"):
            if debug is not None:
                debug["error_field"] = str(data.get("error"))

        text, source = _extract_llm_text_from_json(data)

        if debug is not None:
            debug["extract_source"] = source
            debug["extracted_len"] = len(text or "")

        return (text or "").strip()

    except Exception as e:
        if debug is not None:
            debug["exception"] = str(e)
        return (
            "Subject: (Error)\n"
            "Sender: (Error)\n"
            f"Summary: Exception while calling LLM: {e}\n"
            "Priority: 5\n"
            f"Actions for {person}: None\n"
        )


# ============================================================
# Punkt 1-4: Coverage-Garantie, Validator, Repair-Pass, Excerpt
# ============================================================
def _extract_marked_block(text: str, begin: str = "<<BEGIN>>", end: str = "<<END>>") -> str:
    """Extrahiert den Block zwischen Markern. Wenn Marker fehlen, gibt den Originaltext zurueck."""
    if not text:
        return ""
    t = text
    b = t.find(begin)
    e = t.find(end)
    if b != -1 and e != -1 and e > b:
        inner = t[b + len(begin):e]
        return inner.strip()
    return t.strip()


def _compact_excerpt(body: str, limit: int = 450) -> str:
    """Kurzer Auszug fuer Debug/Fallback im Report."""
    txt = (body or "").strip()
    if not txt:
        return ""
    txt = re.sub(r"\s+", " ", txt)
    return (txt[:limit] + ("..." if len(txt) > limit else "")).strip()


def _make_fallback_block(email_obj: dict, person: str, reason: str) -> str:
    subj = (email_obj.get("subject") or "").strip() or "(ohne Betreff)"
    sender = (email_obj.get("from") or "").strip() or "(unbekannt)"
    excerpt = _compact_excerpt(email_obj.get("body") or "")
    # Priority bewusst 2: soll sichtbar sein, aber nicht wie Alarmstufe 1.
    block = (
        f"Subject: {subj}\n"
        f"Sender: {sender}\n"
        f"Context: \n"
        f"Addressing: UNKNOWN\n"
        f"Asked-Directly: NO\n"
        f"Priority: 2\n"
        f"LLM-Status: FALLBACK\n"
        f"Actions for {person}: Originalmail oeffnen und pruefen\n"
        f"Summary: LLM Output unbrauchbar ({reason}). Bitte Original-Mail pruefen.\n"
    )
    if excerpt:
        block += f"Raw-Excerpt: {excerpt}\n"
    return block


def _validate_parsed_summary(d):
    errs = []
    try:
        p = int(d.get("priority", 5))
    except Exception:
        p = 5
    if not (1 <= p <= 5):
        errs.append("priority")

    addressing = (d.get("addressing") or "").strip().upper()
    if addressing and addressing not in ALLOWED_ADDRESSING:
        d["addressing"] = "UNKNOWN"

    asked = (d.get("asked") or "").strip().upper()
    if asked and asked not in ALLOWED_ASKED:
        d["asked"] = "NO"

    summary = (d.get("summary") or "").strip()
    if not summary:
        errs.append("summary_empty")

    return (len(errs) == 0), errs


def _canonical_block_from_parsed(parsed: dict, email_obj: dict, person: str, status: str = "OK", raw_excerpt: str = "") -> str:
    """Erzeugt einen sauberen Block (ein Label pro Zeile), egal wie das LLM antwortet."""
    subj = (parsed.get("subject") or "").strip() or (email_obj.get("subject") or "").strip() or "(ohne Betreff)"
    sender = (parsed.get("sender") or "").strip() or (email_obj.get("from") or "").strip() or "(unbekannt)"

    addressing = (parsed.get("addressing") or "UNKNOWN").strip().upper()
    if addressing not in ALLOWED_ADDRESSING:
        addressing = "UNKNOWN"

    asked = (parsed.get("asked") or "NO").strip().upper()
    if asked not in ALLOWED_ASKED:
        asked = "NO"

    try:
        prio = int(parsed.get("priority", 5))
    except Exception:
        prio = 5
    if prio < 1 or prio > 5:
        prio = 5

    context = (parsed.get("context") or "").strip()
    actions = (parsed.get("actions") or "").strip() or "Keine."
    summary = (parsed.get("summary") or "").strip() or "Unklar. Bitte Original-Mail pruefen."

    # Excerpt nur bei Bedarf: Repair/Fallback oder P1/P2
    show_excerpt = bool(raw_excerpt) and (status in ("REPAIRED", "FALLBACK") or prio in (1, 2))

    block = (
        f"Subject: {subj}\n"
        f"Sender: {sender}\n"
        f"Context: {context}\n"
        f"Addressing: {addressing}\n"
        f"Asked-Directly: {asked}\n"
        f"Priority: {prio}\n"
        f"LLM-Status: {status}\n"
        f"Actions for {person}: {actions}\n"
        f"Summary: {summary}\n"
    )
    if show_excerpt:
        block += f"Raw-Excerpt: {raw_excerpt}\n"
    return block


def _ollama_generate(model: str, prompt: str, ollama_url: str, num_predict: int = 4000, debug: dict | None = None) -> str:
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 32768,
            "num_predict": num_predict,
        },
    }
    resp = requests.post(ollama_url, json=payload, headers=headers, timeout=180)
    if debug is not None:
        debug["http_status"] = resp.status_code
        debug["resp_text_len"] = len(resp.text or "")
        debug["resp_text_head"] = (resp.text or "")[:800]

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    if debug is not None:
        debug["json_keys"] = sorted([str(k) for k in data.keys()])
        if "done_reason" in data:
            debug["done_reason"] = str(data.get("done_reason"))
        if "model" in data:
            debug["server_model"] = str(data.get("model"))
        if "thinking" in data and isinstance(data.get("thinking"), str):
            debug["thinking_len"] = len(data.get("thinking") or "")

    text, source = _extract_llm_text_from_json(data)
    if debug is not None:
        debug["extract_source"] = source
        debug["extracted_len"] = len(text or "")
        if source == "thinking" and isinstance(data.get("response"), str) and not (data.get("response") or "").strip():
            debug["note"] = "response leer, nutze thinking als Input (wird per Repair in Struktur gebracht)"

    return (text or "").strip()


def _repair_summary_via_ollama(model: str, person: str, email_text: str, broken_output: str, ollama_url: str, debug: dict | None = None) -> str:
    """Zweiter Pass: nur Format-Repair. Sehr strikt, damit Parser wieder sauber arbeitet."""
    repair_prompt = (
        "Du bist ein strikter Formatter. Antworte NUR mit dem Block zwischen <<BEGIN>> und <<END>>.\n"
        "Keine Erklaerungen, kein Markdown, keine Zusatzzeilen.\n\n"
        "Format (alle Labels muessen genau einmal vorkommen):\n"
        f"<<BEGIN>>\n"
        "Subject: ...\n"
        "Sender: ...\n"
        "Context: ...\n"
        "Addressing: DIRECT | CC | GROUP | LIST | UNKNOWN\n"
        "Asked-Directly: YES | NO\n"
        "Priority: 1 | 2 | 3 | 4 | 5\n"
        f"Actions for {person}: ...\n"
        "Summary: ...\n"
        "<<END>>\n\n"
        "DEFAULTS wenn unklar: Addressing=UNKNOWN, Asked-Directly=NO, Priority=5, Actions=Keine., Summary=Unklar. Bitte Original-Mail pruefen.\n\n"
        "E-Mail (inklusive Historie als Kontext):\n"
        "-----\n"
        + email_text[:12000] +
        "\n-----\n\n"
        "Zu reparierender Modell-Output:\n"
        "-----\n"
        # Bug Fix: _tail_text wird jetzt aus utils importiert (war vorher undefiniert)
        + _tail_text((broken_output or ""), 6000) +
        "\n-----\n"
    )
    return _ollama_generate(model, repair_prompt, ollama_url, num_predict=4000, debug=debug)


def _analyze_email_guaranteed(model: str, email_obj: dict, person: str, ollama_url: str, prompt_base: str, debug: dict | None = None) -> str:
    """Hauptlogik: garantiert pro E-Mail genau einen gueltigen Block."""
    # Mailtext fuer LLM: Header + Body
    mail_text = []
    mail_text.append(f"Subject: {email_obj.get('subject','')}")
    mail_text.append(f"From: {email_obj.get('from','')}")
    mail_text.append(f"To: {email_obj.get('to','')}")
    if email_obj.get("cc"):
        mail_text.append(f"Cc: {email_obj.get('cc','')}")
    mail_text.append("")
    mail_text.append(email_obj.get("body", "") or "")
    email_text = "\n".join(mail_text).strip()

    raw_excerpt = _compact_excerpt(email_obj.get("body") or "")

    # 1) erster Versuch
    try:
        stage0 = {} if debug is not None else None
        out0 = analyze_email_via_ollama(model, email_text, person, ollama_url, prompt_base, debug=stage0)
        if debug is not None:
            debug['stage0'] = stage0
    except Exception as e:
        if debug is not None:
            debug['final_status'] = 'FALLBACK'
            debug['fallback_reason'] = f'Exception: {e}'
        return _make_fallback_block(email_obj, person, f"Exception: {e}")

    if not (out0 or "").strip():
        if debug is not None:
            debug['final_status'] = 'FALLBACK'
            debug['fallback_reason'] = 'leere Antwort'
        return _make_fallback_block(email_obj, person, "leere Antwort")

    block0 = _extract_marked_block(out0)
    parsed0 = _parse_llm_summary_block(block0)
    ok0, _errs0 = _validate_parsed_summary(parsed0)
    if debug is not None:
        debug['validate0_ok'] = bool(ok0)
        debug['validate0_errors'] = list(_errs0)

    if ok0:
        if debug is not None:
            debug['final_status'] = 'OK'
        return _canonical_block_from_parsed(parsed0, email_obj, person, status="OK", raw_excerpt=raw_excerpt)

    # 2) Repair-Pass
    try:
        stage1 = {} if debug is not None else None
        repaired = _repair_summary_via_ollama(model, person, email_text, out0, ollama_url, debug=stage1)
        if debug is not None:
            debug['stage1'] = stage1
        block1 = _extract_marked_block(repaired)
        parsed1 = _parse_llm_summary_block(block1)
        ok1, _errs1 = _validate_parsed_summary(parsed1)
        if debug is not None:
            debug['validate1_ok'] = bool(ok1)
            debug['validate1_errors'] = list(_errs1)
        if ok1:
            if debug is not None:
                debug['final_status'] = 'REPAIRED'
            return _canonical_block_from_parsed(parsed1, email_obj, person, status="REPAIRED", raw_excerpt=raw_excerpt)
    except Exception as e:
        if debug is not None:
            debug['repair_exception'] = str(e)
        log.debug("Repair-Pass fehlgeschlagen: %s", e)

    if debug is not None:
        debug['final_status'] = 'FALLBACK'
        debug['fallback_reason'] = 'Repair gescheitert / unparsebar'
    return _make_fallback_block(email_obj, person, "Repair gescheitert / unparsebar")
