# 01 · Give exact comparison one meaning

**Objective.** Close a reproduced false match and prevent an agent retrying a completed pixel comparison as a broken tool. Scope covers the diagnostic and agent-facing replay surfaces. The independent correction gate remains the acceptance authority.

**Evidence.** In `gfx_independent_replay.final_comparison_report`, `pass` under a caller-supplied policy maps to `match`. Reproduce from the repository root without GPU work:

```python
import json, tempfile
from pathlib import Path
from tests.test_m7_diagnosis import DiagnosisFixture

with tempfile.TemporaryDirectory() as tmp:
    f = DiagnosisFixture(Path(tmp))
    f.alter_candidate(offset=0, delta=1)
    print(f.diagnose()["classification"])
    policy = json.loads(Path("analysis/m6/comparison-policy.json").read_text())
    policy["thresholds"] = dict(max_channel_error=1, mismatched_fraction=1, mean_abs_error=1)
    path = Path(tmp) / "policy.json"
    path.write_text(json.dumps(policy))
    report = f.diagnose(policy=path)
    print(report["classification"], report["pixels"]["final"]["statistics"]["mismatched_pixels"])
```

On the review head this prints `qualified_difference` then `qualified_match 1`. Case 616's independent report passed through the adapter also produces `diagnostic_complete=true/failure_kind=tool_failure` for an actually completed divergent replay with unverified identity. The separate window control in assessment exhibits a real 1,445,806-pixel alpha discrepancy hidden by the pilot snippets. [Diagnosis](../../../automation/gfx_independent_replay.py) · [adapter](../../../automation/agent_tools.py) · [checker](../../../automation/correction_accept.py)

**Implementation order**

1. Keep `m6_compare.compare` policy output and its detailed metrics available to its current callers. In the shared M7 pixel projection, compute equality from valid full-frame dimensions, four-channel data, zero raw mismatched pixels and zero max error. Expose a policy pass separately. Reject missing stats, incompatible layouts or invalid evidence. Only documented storage conversion may precede the comparison.
2. In `analyse_frame/classify_diagnosis` require the explicit positive states for capture identity, execution, provenance, coverage and checkpoint reconciliation to claim qualified match. Unknown states produce incomplete/invalid outcomes. Keep the explicit divergent, validated-but-partial and fault-experiment branches. Check `_replay_locked/classify_replay` for a bypass: project canonical qualification into the public replay result. Preserve legacy status/exit meanings for scripts via a versioned projection if needed. Never elevate standalone unverified identity or a state-policy override into independently accepted equivalence.
3. Map `agent_tools.eu4_replay(independent)` using the same canonical outcome mapping as `diagnose`. Interpret the producer's `ok` reconciliation status, propagate separate execution, evidence, pixels, identity and eligibility. A comparison can succeed and be divergent, or have valid execution and missing qualification. Report a tool failure only on an execution/tool error. Avoid resetting top-level flags after constructing the envelope. Map existing CLI exit codes deliberately and test the DSH bridge contract.
4. Use `compact_diagnosis` and the existing bounded-envelope machinery for the default agent view. Include report path, case/stream, observed event and actual writer/resource, authority/coverage, mismatches/max/alpha and one next action. `--full` returns legacy detail. Render matrix/CSV from reconciled checkpoint rows and graph writers with a tested projection; do not infer writers by subtracting one. Reuse the existing report and `m6_compare` for archived raw comparisons. Use `independent` for fresh checkpoints, `diagnose` for existing Metal output, `frame` for raw execution. Correct the tool help in both Python and `dsh/eu4-tools-plugin/lib/index.js`.
5. Apply malformed container hardening already identified as issue #233 where sharing the validation boundary. Keep final-only eligibility explicit and separately test the permitted final-only contract. A compact report is an index into evidence, never an input substituted for recomputation.

**Verification and done.** Use the roadmap's runner commands with `tests.test_m7_diagnosis`, `tests.test_gfx_replay_checkpoints`, `tests.test_agent_tools`, then `tests.test_correction_accept`, `tests.test_m7_pixel_diagnostics` and `tests.test_m7_checkpoint_provenance`; run the DSH tools tests. Require a blue-only and alpha-only one-byte delta, permissive policy, invalid/unknown axes, diagnostic-only checkpoint, exact final with wrong intermediate, stale/skipped GPU output, unverified standalone identity, fault and real exact positive. The case-616 compact replay must retain the 14→15 attribution within the existing 8,192-byte compact budget, without inventing an execution failure. CSV rows 11/13 must identify the request's writer lists. Ship after caller/exit snapshot comparisons, fresh/frozen checker parity and integrated gate. If legacy readers break, retain the old serializer behind an explicit version while serving truthful qualification in both.
