"""
utils.py -- Utility functions for file operations, logging, and text processing.

Leaf module with no internal package dependencies.
Provides basic tools used by multiple other modules in the package.
"""

# ============================================================
# External dependencies
# ============================================================
import os
import json
import logging


# ============================================================
# Logging
# ============================================================
# Goal: not too noisy in normal operation.
# Set ENV EMAIL_REPORT_LOGLEVEL=DEBUG for more output.
LOGLEVEL = os.environ.get("EMAIL_REPORT_LOGLEVEL", "INFO").upper().strip()
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("email_report")


# ============================================================
# Helper functions: safe file handling (with logging)
# ============================================================
def safe_remove(path: str) -> None:
    """Deletes file if it exists."""
    try:
        os.remove(path)
        log.debug("Removed file: %s", path)
    except FileNotFoundError:
        log.debug("File not found (ok): %s", path)
    except Exception as e:
        log.debug("Could not remove %s: %s", path, e)


def ensure_mode_0600(path: str) -> None:
    """Best-effort chmod to 0600. May be ineffective on Windows or some mounts."""
    try:
        os.chmod(path, 0o600)
    except Exception as e:
        log.debug("chmod(0600) failed for %s: %s", path, e)


def append_secure(path: str, text: str) -> None:
    """Appends text to file, best effort 0600."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(text)
    finally:
        ensure_mode_0600(path)


def write_jsonl(path: str, obj: dict) -> None:
    """Writes a JSON object as a single line (JSONL)."""
    try:
        line = json.dumps(obj, ensure_ascii=False)
    except Exception:
        line = json.dumps({"error": "could_not_serialize", "repr": repr(obj)}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_secure(path: str, text: str) -> None:
    """Overwrites file, best effort 0600."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    finally:
        ensure_mode_0600(path)


def load_prompt_file(path: str) -> str:
    """
    Loads a prompt from a text file.
    - UTF-8
    - Strips a potential UTF-8 BOM
    - Ensures the prompt ends with a newline
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        txt = f.read()

    txt = txt.strip()
    if not txt.endswith("\n"):
        txt += "\n"
    return txt



def _tail_text(text: str, limit: int = 6000) -> str:
    """Returns the last `limit` characters of text."""
    if len(text) <= limit:
        return text
    return text[-limit:]
