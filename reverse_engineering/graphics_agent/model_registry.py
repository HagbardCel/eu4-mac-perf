"""Model provider profiles for investigation modes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def load_models_config() -> dict[str, Any]:
    path = CONFIG_DIR / "models.json"
    return json.loads(path.read_text())


def profile_for_mode(mode: str) -> dict[str, Any]:
    cfg = load_models_config()
    profiles = cfg.get("profiles", {})
    key = {
        "discovery": "discovery",
        "reconstruct": "reconstruction",
        "verify": "verification",
        "report": "reporting",
    }.get(mode, "discovery")
    return profiles.get(key, profiles["discovery"])


def validate_profile(profile: dict[str, Any]) -> None:
    thinking = profile.get("thinking")
    alias = profile.get("api_model_alias")
    provider = profile.get("provider", "openai")
    if thinking and provider not in ("deepseek", "openai"):
        raise ValueError(f"thinking=true not supported for provider {provider}")
    if alias and provider != "deepseek":
        raise ValueError(f"api_model_alias is only supported for deepseek, not {provider}")
    if alias and provider == "deepseek":
        raise ValueError(
            "api_model_alias is set but not applied by model_string; fix models.json or implement alias mapping"
        )


def model_string(profile: dict[str, Any]) -> str:
    validate_profile(profile)
    provider = profile.get("provider", "openai")
    model = profile.get("model", "gpt-4.1-mini")
    if provider == "deepseek":
        return f"deepseek:{model}"
    return f"openai:{model}"
