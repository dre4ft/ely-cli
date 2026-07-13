"""
Configuration loader — reads ely.yaml, env vars, or defaults.
No database dependency. No web framework.
"""

import os
import yaml
from pathlib import Path

_DEFAULTS = {
    "provider": {
        "type": "openai",  # openai, ollama, lmstudio
        "model": "gpt-4o-mini",
        "url": "https://api.openai.com/v1",
        "api_key": "",
    },
    "pro_provider": {
        "type": "openai",
        "model": "gpt-4o",
        "url": "https://api.openai.com/v1",
        "api_key": "",
    },
    "agent": {
        "max_turns": 8,
        "name": "Ely",
        "language": "fr",
    },
    "memory": {
        "dir": "~/.ely/memory",
        "compaction_rounds": 10,
    },
    "tools": {
        "disabled": "",  # Comma-separated tool names to disable, e.g. "bash,web_search"
    },
    "mcp": {
        "servers": [],
    },
}

_config = None


def _find_config():
    paths = [
        os.path.join(os.getcwd(), "ely.yaml"),
        os.path.join(Path.home(), ".ely", "config.yaml"),
        os.path.join(Path.home(), ".ely.yaml"),
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    return None


def _load():
    global _config
    if _config is not None:
        return _config

    _config = {}
    for section, params in _DEFAULTS.items():
        for key, value in params.items():
            _config[f"{section}.{key}"] = value

    cfg_path = _find_config()
    if cfg_path:
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        for section, params in data.items():
            if isinstance(params, dict):
                for key, value in params.items():
                    _config[f"{section}.{key}"] = str(value)

    # Env var overrides: ELY_PROVIDER_TYPE, ELY_PROVIDER_MODEL, etc.
    for k, v in os.environ.items():
        if k.startswith("ELY_"):
            key = k[4:].lower().replace("__", ".")
            _config[key] = v

    return _config


def get(section: str, key: str, default: str = "") -> str:
    return _load().get(f"{section}.{key}", default)


def get_bool(section: str, key: str, default: bool = False) -> bool:
    val = get(section, key, str(default)).lower()
    return val in ("1", "true", "yes", "on")


def get_int(section: str, key: str, default: int = 0) -> int:
    try:
        return int(get(section, key, str(default)))
    except (ValueError, TypeError):
        return default


def get_provider_config(slot: str = "provider") -> dict:
    """Return full provider config dict for a slot."""
    prefix = f"{slot}."
    cfg = {}
    for k in ("type", "model", "url", "api_key"):
        cfg[k] = get(slot, k, _DEFAULTS.get(slot, {}).get(k, ""))
    # Fallback: try env var for API key
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get("LLM_API_KEY", "")
    return cfg
