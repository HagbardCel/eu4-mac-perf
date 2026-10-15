"""Model provider profiles for investigation modes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.settings import ModelSettings

CONFIG_DIR = Path(__file__).resolve().parent / "config"

DEEPSEEK_ALIAS_TO_WIRE = {
    "deepseek-flash": "deepseek-flash",
}


@dataclass(frozen=True)
class EffectiveModelProfile:
    raw_profile: dict[str, Any]
    provider: str
    wire_model_id: str
    wire_model_key: str
    thinking: bool
    api_mode: str


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


def _wire_model_id(profile: dict[str, Any]) -> str:
    provider = profile.get("provider", "openai")
    model = profile.get("model", "gpt-4.1-mini")
    if provider == "deepseek":
        alias = profile.get("api_model_alias")
        if alias:
            mapped = DEEPSEEK_ALIAS_TO_WIRE.get(alias, alias)
            return mapped
        return model
    return model


def resolve_effective_profile(profile: dict[str, Any]) -> EffectiveModelProfile:
    provider = profile.get("provider", "openai")
    wire_id = _wire_model_id(profile)
    thinking = bool(profile.get("thinking", False))
    api_mode = str(profile.get("structured_output") or "default")
    return EffectiveModelProfile(
        raw_profile=profile,
        provider=provider,
        wire_model_id=wire_id,
        wire_model_key=f"{provider}:{wire_id}",
        thinking=thinking,
        api_mode=api_mode,
    )


def validate_profile(profile: dict[str, Any]) -> None:
    thinking = profile.get("thinking")
    alias = profile.get("api_model_alias")
    provider = profile.get("provider", "openai")
    if thinking and provider not in ("deepseek", "openai"):
        raise ValueError(f"thinking=true not supported for provider {provider}")
    if alias and provider != "deepseek":
        raise ValueError(f"api_model_alias is only supported for deepseek, not {provider}")
    if alias and alias not in DEEPSEEK_ALIAS_TO_WIRE and alias != profile.get("model"):
        # Unknown alias still allowed if it matches explicit wire id string
        if not alias.replace("-", "").isalnum():
            raise ValueError(f"unsupported api_model_alias {alias}")


def model_string(profile: dict[str, Any]) -> str:
    validate_profile(profile)
    effective = resolve_effective_profile(profile)
    return effective.wire_model_key


def model_settings_for_profile(profile: dict[str, Any]) -> ModelSettings:
    effective = resolve_effective_profile(profile)
    return ModelSettings(thinking=effective.thinking)
