"""Run one function package through the certified P3 controller.

The seam's job is deliberately small:

* describe the package (or refuse it);
* wrap the caller's P3 agent adapter in :class:`FunctionAgentAdapter`;
* hand a normal :class:`oracle.slice.controller.RunConfig` to the certified
  controller and return its result unchanged.

It does not implement budget, attempts, deadlines, process supervision, freezing,
certification or receipts - all of those stay in ``oracle/slice``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.function_agent import catalog
from oracle.function_agent.adapter import FunctionAgentAdapter
from oracle.function_agent.packages import FunctionPackage, PackageRefused, describe_package
from oracle.slice import controller, harness

CLAIM_SCOPE = (
    "The G3a seam drives the certified P3 controller, bridge, budget ledger, process "
    "supervisor, workspace materialization and receipt chain for the synthetic "
    "'synth:apply' package only. It does not support arbitrary reconstructed C++ "
    "functions: the P3 workspace remains specialised to candidate.c/synth.h, and "
    "generalizing it is G3b work."
)


@dataclass(frozen=True)
class SeamClaim:
    """The honest scope statement returned with every seam result."""

    function_id: str
    downstream: str
    synthetic: bool
    evidence_tier: str
    scope: str = CLAIM_SCOPE
    arbitrary_cpp_supported: bool = False
    generic_workspace: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "scope": self.scope,
            "downstream": self.downstream,
            "synthetic": self.synthetic,
            "evidence_tier": self.evidence_tier,
            "arbitrary_cpp_supported": self.arbitrary_cpp_supported,
            "generic_workspace": self.generic_workspace,
        }


@dataclass
class SeamResult:
    """A refusal, or the untouched result of a certified-P3 run."""

    status: str
    claim: dict[str, Any]
    package: FunctionPackage | None = None
    refusal: dict[str, str] | None = None
    run: controller.RunResult | None = None
    isolation: dict[str, Any] | None = field(default=None)

    @property
    def certified(self) -> bool:
        return self.run is not None and self.status == controller.STATUS_CERTIFIED

    @property
    def receipt(self) -> dict[str, Any] | None:
        return None if self.run is None else self.run.receipt

    @property
    def receipt_path(self) -> Path | None:
        return None if self.run is None else self.run.receipt_path

    @property
    def run_dir(self) -> Path | None:
        return None if self.run is None else self.run.run_dir

    @property
    def abort_reason(self) -> str | None:
        return None if self.run is None else self.run.abort_reason

    def summary(self) -> dict[str, Any]:
        """A compact, JSON-safe account of what this seam call produced."""
        return {
            "status": self.status,
            "certified": self.certified,
            "claim": dict(self.claim),
            "package": None if self.package is None else self.package.describe(),
            "refusal": self.refusal,
            "isolation_ok": None if self.isolation is None else self.isolation["ok"],
            "receipt_sha256": None if self.receipt is None else self.receipt["sha256"],
        }


def _claim(function_id: str, *, downstream: str, synthetic: bool,
           evidence_tier: str) -> dict[str, Any]:
    return SeamClaim(function_id=function_id, downstream=downstream, synthetic=synthetic,
                     evidence_tier=evidence_tier).as_dict()


def run_function_package(function_id: str, *, run_id: str, base_dir: Path, inner: Any,
                         downstream: str, synthetic: bool, budget_calls: int = 40,
                         max_attempts: int = 4, wall_seconds: float = 120.0,
                         finalization_seconds: float = 60.0,
                         timeout_s: float = harness.DEFAULT_TIMEOUT_S, cc: str = "cc",
                         fixtures_root: Path | None = None) -> SeamResult:
    """Describe ``function_id`` and run it through the certified P3 controller.

    ``downstream`` and ``synthetic`` are required keyword arguments: a caller must
    say what actually produced the edits, and whether that producer was a mock,
    stub or synthetic path. The label is recorded in the returned claim and, via
    ``RunConfig.model``, inside the controller-sealed receipt.
    """
    try:
        package = describe_package(function_id, fixtures_root=fixtures_root)
    except PackageRefused as refused:
        return SeamResult(
            status=refused.status,
            claim=_claim(function_id, downstream=downstream, synthetic=synthetic,
                         evidence_tier="none"),
            refusal=refused.as_dict(),
        )

    adapter = FunctionAgentAdapter(package, inner)
    config = controller.RunConfig(
        run_id=run_id,
        candidate_src=package.candidate_src,
        base_dir=Path(base_dir),
        budget_calls=budget_calls,
        max_attempts=max_attempts,
        wall_seconds=wall_seconds,
        finalization_seconds=finalization_seconds,
        timeout_s=timeout_s,
        cc=cc,
        fixtures_root=package.fixtures_root,
        # The controller records this verbatim in the sealed receipt, so the
        # downstream producer and its synthetic nature are bound to the evidence.
        model={
            "provider": "function-agent",
            "id": downstream,
            "variant": None,
            "synthetic": bool(synthetic),
            "evidence_tier": package.evidence_tier,
            "arbitrary_cpp_supported": package.arbitrary_cpp_supported,
        },
    )
    run = controller.run(config, adapter)
    isolation = catalog.held_out_report(run.run_dir / "workspace",
                                        fixtures_root=package.fixtures_root)
    status = run.status
    if status == controller.STATUS_CERTIFIED and not isolation["ok"]:
        status = controller.STATUS_ACCEPTANCE_GATE_FAILED
    return SeamResult(
        status=status,
        claim=_claim(function_id, downstream=downstream, synthetic=synthetic,
                     evidence_tier=package.evidence_tier),
        package=package,
        run=run,
        isolation=isolation,
    )
