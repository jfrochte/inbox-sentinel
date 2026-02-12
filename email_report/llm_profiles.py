"""
llm_profiles.py -- LLM parameter profiles (extraction vs. creative).

Leaf module with no internal package dependencies.
Loads profiles from llm_profiles.json, falls back to hardcoded defaults.
"""

import json
import os

_PROFILES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "llm_profiles.json",
)

_DEFAULT_PROFILES = {
    "extraction": {
        "num_ctx": 32768,
        "num_ctx_thread": 65536,
        "num_predict": 4000,
        "temperature": 0.1,
        "top_p": 0.85,
    },
    "creative": {
        "num_ctx": 32768,
        "num_ctx_thread": 65536,
        "num_predict": 4000,
        "temperature": 0.7,
        "top_p": 0.9,
    },
}


def load_llm_profiles() -> dict:
    """Loads LLM profiles from llm_profiles.json. Falls back to defaults."""
    try:
        with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "extraction" in data and "creative" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return dict(_DEFAULT_PROFILES)


def profile_to_options(profile: dict, is_thread: bool = False) -> dict:
    """Converts a profile dict to an Ollama options dict.

    Uses num_ctx_thread when is_thread=True, otherwise num_ctx.
    """
    ctx = profile.get("num_ctx_thread", 65536) if is_thread else profile.get("num_ctx", 32768)
    opts = {
        "num_ctx": ctx,
        "num_predict": profile.get("num_predict", 4000),
    }
    if "temperature" in profile:
        opts["temperature"] = profile["temperature"]
    if "top_p" in profile:
        opts["top_p"] = profile["top_p"]
    return opts
