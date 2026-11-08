"""Versioned, independently qualified draw-evidence contracts.

The trusted checker loads exactly one contract version. A version is immutable:
new validators are added in a new module and promoted through a separate
qualification campaign. The dispatcher in ``automation.gfx_draw_accept`` is
fail-closed; an unregistered required field is never accepted.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable

from automation.gfx_contracts.api import ContractContext, ContractError

VERSION_RE = re.compile(r"^v[0-9]+$")


@dataclass(frozen=True)
class Contract:
    version: str
    validators: dict[str, Callable[[dict, ContractContext], str | None]]


def load_contract(version: str) -> Contract:
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ContractError(f"invalid contract version: {version!r}")
    module_name = f"automation.gfx_contracts.{version}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ContractError(f"contract module unavailable: {module_name}: {exc}") from exc
    validators = getattr(module, "VALIDATORS", None)
    if not isinstance(validators, dict):
        raise ContractError(f"{module_name} has no VALIDATORS mapping")
    for name, validator in validators.items():
        if not isinstance(name, str) or not name:
            raise ContractError(f"{module_name} has an invalid field name")
        if not callable(validator):
            raise ContractError(f"{module_name} validator for {name!r} is not callable")
    return Contract(version=version, validators=dict(validators))


def merge_validators(
    base: dict[str, Callable[[dict, ContractContext], str | None]],
    contract: Contract,
) -> dict[str, Callable[[dict, ContractContext], str | None]]:
    overlap = sorted(set(base) & set(contract.validators))
    if overlap:
        raise ContractError(
            "contract redefines base validators: " + ", ".join(overlap)
        )
    merged = dict(base)
    merged.update(contract.validators)
    return merged


__all__ = [
    "Contract",
    "ContractContext",
    "ContractError",
    "VERSION_RE",
    "load_contract",
    "merge_validators",
]
