"""Resolve whether E1's public certifier is available for integration tests."""
from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Callable, Literal

CertifyStatus = Literal["absent", "ready", "broken"]


def certify_import_status() -> tuple[CertifyStatus, str | None]:
    if importlib.util.find_spec("oracle.certify") is None:
        return "absent", None
    try:
        module = importlib.import_module("oracle.certify")
    except ImportError as error:
        return "broken", str(error)
    if not hasattr(module, "certify_session"):
        return "broken", "oracle.certify.certify_session is missing"
    return "ready", None


def load_certify_session() -> Callable[..., Any] | None:
    status, _ = certify_import_status()
    if status != "ready":
        return None
    module = importlib.import_module("oracle.certify")
    return module.certify_session
