"""
threading.py – E-Mail-Threading: gruppiert Mails zu Konversationen.

Blattmodul ohne Paket-Abhaengigkeiten (nur re).

Algorithmus: Union-Find mit zwei Paessen:
  1) Header-basiert (Message-ID / In-Reply-To / References)
  2) Subject-Fallback (konservativ, min 8 Zeichen normalisierter Subject)
"""

import re

# ============================================================
# Subject-Normalisierung
# ============================================================
_PREFIX_RE = re.compile(
    r"^(?:"
    r"re|aw|antwort|antw|sv|vs|ref|fwd|fw|wg|wtr"
    r")\s*:\s*",
    re.IGNORECASE,
)


def normalize_subject(subject: str) -> str:
    """Strippt Reply-/Forward-Prefixe, normalisiert Whitespace, lowercase."""
    s = (subject or "").strip()
    changed = True
    while changed:
        changed = False
        m = _PREFIX_RE.match(s)
        if m:
            s = s[m.end():]
            changed = True
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# ============================================================
# Union-Find
# ============================================================
class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# ============================================================
# Threading
# ============================================================
def _sort_key(email: dict):
    """Sortierschluessel: ISO-Datum, dann UID als Fallback."""
    return (email.get("date") or "", email.get("uid") or "")


def group_into_threads(emails: list[dict]) -> list[list[dict]]:
    """
    Gruppiert E-Mails in Threads.

    Pass 1: Header-basiert (Message-ID, In-Reply-To, References)
    Pass 2: Subject-Fallback (nur fuer Mails, die noch keiner
            Header-Gruppe mit 2+ Mitgliedern angehoeren)

    Rueckgabe: Liste von Threads, jeder Thread ist eine chronologisch
    sortierte Liste von Email-Dicts. Threads untereinander nach
    aeltestem Datum sortiert.
    """
    n = len(emails)
    if n == 0:
        return []

    uf = _UnionFind(n)

    # Message-ID -> Index Map
    mid_to_idx: dict[str, int] = {}
    for i, e in enumerate(emails):
        mid = (e.get("message_id") or "").strip()
        if mid:
            mid_to_idx[mid] = i

    # Pass 1: Header-basierte Verknuepfung
    for i, e in enumerate(emails):
        # In-Reply-To
        irt = (e.get("in_reply_to") or "").strip()
        if irt and irt in mid_to_idx:
            uf.union(i, mid_to_idx[irt])

        # References
        for ref in (e.get("references") or []):
            ref = ref.strip()
            if ref and ref in mid_to_idx:
                uf.union(i, mid_to_idx[ref])

    # Bestimme, welche Mails in Header-Gruppen mit 2+ Mitgliedern sind
    header_groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        header_groups.setdefault(root, []).append(i)

    in_header_group = set()
    for members in header_groups.values():
        if len(members) >= 2:
            in_header_group.update(members)

    # Pass 2: Subject-Fallback (konservativ)
    subj_to_idx: dict[str, int] = {}
    for i, e in enumerate(emails):
        if i in in_header_group:
            continue
        ns = normalize_subject(e.get("subject") or "")
        if len(ns) < 8:
            continue
        if ns in subj_to_idx:
            uf.union(i, subj_to_idx[ns])
        else:
            subj_to_idx[ns] = i

    # Gruppen sammeln
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(emails[i])

    # Threads intern chronologisch sortieren
    threads = []
    for members in groups.values():
        members.sort(key=_sort_key)
        threads.append(members)

    # Threads untereinander nach aeltestem Datum sortieren
    threads.sort(key=lambda t: _sort_key(t[0]))

    return threads


# ============================================================
# Thread-Formatierung fuer LLM
# ============================================================
def format_thread_for_llm(thread: list[dict]) -> str:
    """
    Formatiert einen Thread als Text fuer das LLM.

    Einzelne Mail: Standard-Format (Subject/From/To/Cc + Body)
    Thread (2+): Chronologisch mit === Message X of N === Markern
    """
    if len(thread) == 1:
        e = thread[0]
        parts = []
        parts.append(f"Subject: {e.get('subject', '')}")
        parts.append(f"From: {e.get('from', '')}")
        parts.append(f"To: {e.get('to', '')}")
        if e.get("cc"):
            parts.append(f"Cc: {e.get('cc', '')}")
        parts.append("")
        parts.append(e.get("body", "") or "")
        return "\n".join(parts).strip()

    n = len(thread)
    parts = []
    parts.append(f"This is an email thread with {n} messages, shown in chronological order.\n")

    for i, e in enumerate(thread, start=1):
        parts.append(f"=== Message {i} of {n} ===")
        parts.append(f"Subject: {e.get('subject', '')}")
        parts.append(f"From: {e.get('from', '')}")
        parts.append(f"To: {e.get('to', '')}")
        if e.get("cc"):
            parts.append(f"Cc: {e.get('cc', '')}")
        if e.get("date"):
            parts.append(f"Date: {e.get('date')}")
        parts.append("")
        parts.append(e.get("body", "") or "")
        parts.append("")

    return "\n".join(parts).strip()
