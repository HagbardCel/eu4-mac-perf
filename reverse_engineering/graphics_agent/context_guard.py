"""Pre-send context size guard via PydanticAI before_model_request hooks."""

from __future__ import annotations

import json
import os
from typing import Any

from reverse_engineering.paths import ReError

DEFAULT_MAX_CONTEXT_BYTES = int(os.environ.get("EU4_AGENT_MAX_CONTEXT_BYTES", "350000"))


class ContextBudgetExceeded(ReError):
    pass


def _json_size(obj: Any) -> int:
    return len(json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))


def estimate_request_bytes(request_context: Any) -> int:
    """Conservative serialized size of the outbound model request."""
    total = 0
    params = getattr(request_context, "model_request_parameters", None)
    if params is not None:
        for attr in ("instruction_parts", "function_tools", "builtin_tools", "output_tools"):
            part = getattr(params, attr, None)
            if part is not None:
                total += _json_size(part)
    messages = getattr(request_context, "messages", None) or []
    total += _json_size(messages)
    settings = getattr(request_context, "model_settings", None)
    if settings is not None:
        total += _json_size(settings)
    return total


def check_request_context(request_context: Any, *, max_bytes: int | None = None) -> None:
    limit = max_bytes if max_bytes is not None else DEFAULT_MAX_CONTEXT_BYTES
    size = estimate_request_bytes(request_context)
    if size > limit:
        raise ContextBudgetExceeded(
            f"model request context {size} bytes exceeds limit {limit}; reduce tool output or history"
        )


def build_context_guard_hooks(max_bytes: int | None = None):
    from pydantic_ai.capabilities import Hooks

    hooks = Hooks()
    limit = max_bytes if max_bytes is not None else DEFAULT_MAX_CONTEXT_BYTES

    @hooks.on.before_model_request
    async def _guard(_ctx, request_context):  # type: ignore[no-untyped-def]
        check_request_context(request_context, max_bytes=limit)
        return request_context

    return hooks
