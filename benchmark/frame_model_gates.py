"""Shared, fail-closed release evidence for controller and report consumers."""
from dataclasses import dataclass, field

REQUIRED = ("format_v3", "offline_overhead", "live_counters", "live_sampled",
            "integrity", "origin_integrity", "semantic_coverage", "cadence", "interventions", "control_drift")

@dataclass
class GateEvidence:
    entries: dict = field(default_factory=dict)

    def record(self, name, status, reason, **evidence):
        if status not in ("passed", "failed", "unavailable"):
            raise ValueError(status)
        self.entries[name] = {"status": status, "reason": reason, **evidence}

    def blockers(self, required=REQUIRED):
        return {name: self.entries.get(name, {"status": "unavailable", "reason": "evidence missing"})
                for name in required if self.entries.get(name, {}).get("status") != "passed"}

    def require(self, required=REQUIRED):
        blockers = self.blockers(required)
        if blockers:
            raise ValueError("Release gates block progression: " + "; ".join(
                f"{key}: {value['reason']}" for key, value in blockers.items()))

# The helper outlives the controller deadline by one minute, allowing restoration.
HELPER_SECONDS = 1200
RUN_DEADLINE_SECONDS = 1140

def run_budget(phases, low_power=False, fixed=False, discovery=False):
    # Readiness 180, warmup 90 + bounded 60 extension; every phase has 5-second
    # settling and three 4-second acknowledgement allowances. Cleanup reserves 40.
    durations = [15, 15, 15, 20] + [p['duration_s'] for p in phases]
    if not discovery:
        durations += [30]
    transitions = 0
    if low_power:
        durations += [15, 30, 15]; transitions += 20
        if fixed:
            durations += [15, 30, 15]; transitions += 20
    budget = 180 + 150 + sum(durations) + len(durations)*17 + transitions + 40
    if budget > RUN_DEADLINE_SECONDS:
        raise ValueError(f"Worst-case run budget {budget}s exceeds controller deadline {RUN_DEADLINE_SECONDS}s")
    return budget
