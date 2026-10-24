"""Resolve whether F1's public run_owned is available for integration tests."""
from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Callable, Literal

RunOwnedStatus = Literal["absent", "ready", "broken"]


def run_owned_import_status() -> tuple[RunOwnedStatus, str | None]:
    if importlib.util.find_spec("oracle.shadow") is None:
        return "absent", None
    try:
        module = importlib.import_module("oracle.shadow")
    except ImportError as error:
        return "broken", str(error)
    if not hasattr(module, "run_owned"):
        return "broken", "oracle.shadow.run_owned is missing"
    return "ready", None


def load_run_owned() -> Callable[..., Any] | None:
    status, _ = run_owned_import_status()
    if status != "ready":
        return None
    module = importlib.import_module("oracle.shadow")
    return module.run_owned
