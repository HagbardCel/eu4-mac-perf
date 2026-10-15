"""Shared agent construction (model settings, context guard hooks)."""

from __future__ import annotations

from typing import Any

from reverse_engineering.graphics_agent.context_guard import build_context_guard_hooks
from reverse_engineering.graphics_agent.model_registry import EffectiveModelProfile, model_settings_for_profile


def agent_capabilities(max_context_bytes: int | None = None) -> list[Any]:
    return [build_context_guard_hooks(max_context_bytes)]


def paid_agent_kwargs(effective: EffectiveModelProfile, max_context_bytes: int | None = None) -> dict[str, Any]:
    return {
        "model_settings": model_settings_for_profile(effective.raw_profile),
        "capabilities": agent_capabilities(max_context_bytes),
    }
