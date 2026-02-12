"""
i18n.py -- Internationalization: load translation files, provide t() lookup.

Leaf module with no internal package dependencies.
Translation files live in i18n/ (project root), one JSON per language.
"""

import json
import os

_I18N_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "i18n",
)

_active_lang = "en"
_cache: dict[str, dict] = {}


def _load_lang(lang: str) -> dict:
    """Loads a language file and caches it. Returns {} on error."""
    if lang in _cache:
        return _cache[lang]
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[lang] = data
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache[lang] = {}
        return {}


def set_language(lang: str) -> None:
    """Sets the active language (e.g. 'en', 'de')."""
    global _active_lang
    _active_lang = (lang or "en").strip().lower()


def get_language() -> str:
    """Returns the currently active language code."""
    return _active_lang


def _resolve(data: dict, key: str) -> str | None:
    """Resolves a dotted key like 'report.section_mails' in nested dict."""
    parts = key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current if isinstance(current, str) else None


def t(key: str, **kwargs) -> str:
    """Translates a dotted key with optional format kwargs.

    Fallback chain: active language -> 'en' -> key as-is.
    """
    # Try active language
    val = _resolve(_load_lang(_active_lang), key)
    # Fallback to English
    if val is None and _active_lang != "en":
        val = _resolve(_load_lang("en"), key)
    # Fallback to key
    if val is None:
        return key
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError):
            return val
    return val
