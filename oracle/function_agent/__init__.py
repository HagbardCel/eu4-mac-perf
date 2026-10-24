"""G3a: a function-package adapter seam over the certified P3 slice.

The package is a thin layer: it describes a function package (currently only the
synthetic ``synth:apply`` target), wraps an existing P3 agent adapter so the
agent sees exactly the certified five tools, and delegates the run to
``oracle.slice.controller``.

Nothing here modifies or reimplements certified P3. Budget, process supervision,
workspace isolation, certification and receipt sealing are imported from
``oracle.slice`` and remain the controller's.
"""
from __future__ import annotations

from oracle.function_agent.adapter import FunctionAgentAdapter
from oracle.function_agent.catalog import (
    AGENT_VISIBLE_TOOLS,
    CONTROLLER_ONLY_TOOL_NAMES,
    CatalogBoundTools,
    CatalogError,
    HeldOutLeak,
    assert_isolated,
    catalog_report,
    held_out_report,
    validate_catalog,
)
from oracle.function_agent.packages import (
    SYNTH_APPLY,
    SUPPORTED_FUNCTION_IDS,
    FunctionPackage,
    PackageRefused,
    describe_package,
    supported_packages,
)
from oracle.function_agent.seam import CLAIM_SCOPE, SeamClaim, SeamResult, run_function_package

__all__ = [
    "AGENT_VISIBLE_TOOLS",
    "CLAIM_SCOPE",
    "CONTROLLER_ONLY_TOOL_NAMES",
    "CatalogBoundTools",
    "CatalogError",
    "FunctionAgentAdapter",
    "FunctionPackage",
    "HeldOutLeak",
    "PackageRefused",
    "SYNTH_APPLY",
    "SUPPORTED_FUNCTION_IDS",
    "SeamClaim",
    "SeamResult",
    "assert_isolated",
    "catalog_report",
    "describe_package",
    "held_out_report",
    "run_function_package",
    "supported_packages",
    "validate_catalog",
]
