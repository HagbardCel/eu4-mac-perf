"""Shared, fail-closed release evidence for controller and report consumers."""
from dataclasses import dataclass, field

REQUIRED = (
    "format_v3",
    "offline_causal_admission",
    "live_counters",
    "integrity",
    "origin_integrity",
    "draw_api_coverage",
    "semantic_coverage",
    "cadence",
    "interventions",
    "control_drift",
)

REQUIRED_CALIBRATION_ONLY = (
    "format_v3",
    "offline_causal_admission",
)

REQUIRED_RESIDUAL_DISCOVERY = REQUIRED_CALIBRATION_ONLY + (
    "live_counters",
    "integrity",
    "origin_integrity",
    "cadence",
)

REQUIRED_INTRUSIVE_DIAGNOSTIC = (
    "format_v3",
    "integrity",
    "origin_integrity",
    "cadence",
    "diagnostic_authorization",
    "live_observer_effect",
)

REQUIRED_BY_REPORT_KIND = {
    "calibration_only": REQUIRED_CALIBRATION_ONLY,
    "residual_discovery": REQUIRED_RESIDUAL_DISCOVERY,
    "causal": REQUIRED,
    "intrusive_diagnostic": REQUIRED_INTRUSIVE_DIAGNOSTIC,
}


def required_gates_for_report_kind(report_kind: str) -> tuple[str, ...]:
    return REQUIRED_BY_REPORT_KIND.get(report_kind, REQUIRED)


@dataclass
class GateEvidence:
    entries: dict = field(default_factory=dict)

    def record(self, name, status, reason, **evidence):
        if status not in ("passed", "failed", "unavailable"):
            raise ValueError(status)
        self.entries[name] = {"status": status, "reason": reason, **evidence}

    def blockers(self, required=REQUIRED):
        return {
            name: self.entries.get(name, {"status": "unavailable", "reason": "evidence missing"})
            for name in required
            if self.entries.get(name, {}).get("status") != "passed"
        }

    def require(self, required=REQUIRED):
        blockers = self.blockers(required)
        if blockers:
            raise ValueError(
                "Release gates block progression: "
                + "; ".join(f"{key}: {value['reason']}" for key, value in blockers.items())
            )


# The helper outlives the controller deadline by one minute, allowing restoration.
HELPER_SECONDS = 1200
RUN_DEADLINE_SECONDS = 1140


def intrusive_diagnostic_run_budget(
    phase_count: int = 5,
    phase_duration_s: int = 20,
    *,
    readiness_s: int = 180,
    warmup_s: int = 150,
    forensic_tail_s: int = 20,
    cleanup_s: int = 40,
) -> int:
    """Worst-case controller budget for Phase C R–C–R–C–R plus isolated forensic tail."""
    per_phase_overhead = 17
    settle_s = 5
    durations = [phase_duration_s] * phase_count
    if forensic_tail_s:
        durations.append(forensic_tail_s)
    budget = (
        readiness_s
        + warmup_s
        + sum(durations)
        + len(durations) * (settle_s + per_phase_overhead)
        + cleanup_s
    )
    if budget > RUN_DEADLINE_SECONDS:
        raise ValueError(
            f"Intrusive diagnostic run budget {budget}s exceeds controller deadline {RUN_DEADLINE_SECONDS}s",
        )
    return budget


def run_budget(phases, low_power=False, fixed=False, discovery=False):
    # Readiness 180, warmup 90 + bounded 60 extension; every phase has 5-second
    # settling and three 4-second acknowledgement allowances. Cleanup reserves 40.
    durations = [15, 15, 15, 20] + [p["duration_s"] for p in phases]
    durations += [20 if discovery else 30]  # isolated forensic capture after causal phases
    transitions = 0
    if low_power:
        durations += [15, 30, 15]
        transitions += 20
        if fixed:
            durations += [15, 30, 15]
            transitions += 20
    budget = 180 + 150 + sum(durations) + len(durations) * 17 + transitions + 40
    if budget > RUN_DEADLINE_SECONDS:
        raise ValueError(f"Worst-case run budget {budget}s exceeds controller deadline {RUN_DEADLINE_SECONDS}s")
    return budget
