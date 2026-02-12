"""
threading.py -- Email threading: groups emails into conversations.

Leaf module with no package dependencies (only re).

Algorithm: Union-Find with two passes:
  1) Header-based (Message-ID / In-Reply-To / References)
  2) Subject fallback (conservative, min 8 chars of normalized subject)
"""

import re

# ============================================================
# Subject normalization
# ============================================================
_PREFIX_RE = re.compile(
    r"^(?:"
    r"re|aw|antwort|antw|sv|vs|ref|fwd|fw|wg|wtr"
    r")\s*:\s*",
    re.IGNORECASE,
)


def normalize_subject(subject: str) -> str:
    """Strips reply/forward prefixes, normalizes whitespace, lowercase."""
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
    """Sort key: ISO date, then UID as fallback."""
    return (email.get("date") or "", email.get("uid") or "")


def group_into_threads(emails: list[dict]) -> list[list[dict]]:
    """
    Groups emails into threads.

    Pass 1: header-based (Message-ID, In-Reply-To, References)
    Pass 2: subject fallback (only for mails not yet in a
            header group with 2+ members)

    Returns: list of threads, each thread is a chronologically
    sorted list of email dicts. Threads sorted among each other
    by oldest date.
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

    # Pass 1: header-based linking
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

    # Determine which mails are in header groups with 2+ members
    header_groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        header_groups.setdefault(root, []).append(i)

    in_header_group = set()
    for members in header_groups.values():
        if len(members) >= 2:
            in_header_group.update(members)

    # Pass 2: subject fallback (conservative)
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

    # Collect groups
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(emails[i])

    # Sort threads internally by chronological order
    threads = []
    for members in groups.values():
        members.sort(key=_sort_key)
        threads.append(members)

    # Sort threads among each other by oldest date
    threads.sort(key=lambda t: _sort_key(t[0]))

    return threads


# ============================================================
# Thread formatting for LLM
# ============================================================
def format_thread_for_llm(thread: list[dict]) -> str:
    """
    Formats a thread as text for the LLM.

    Single mail: standard format (Subject/From/To/Cc + Body)
    Thread (2+): chronological with === Message X of N === markers
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
