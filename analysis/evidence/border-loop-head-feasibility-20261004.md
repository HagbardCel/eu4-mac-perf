# Evidence: border loop-head feasibility (offline static RE)

```json
{
  "evidence_kind": "offline_static_re",
  "evidence_id": "border-loop-head-feasibility-20261004",
  "binary_sha256": "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
  "live_game_run": false,
  "record_stream_available": false,
  "classifier_test_evidence": "synthetic_only",
  "static_feasibility": "GO_CONDITIONAL_RUNTIME_GATES",
  "runtime_preconditions": ["GATE0_MULTIDRAW_CONTEXT_SUPPORT"],
  "mutation_authorized": false,
  "roi_gate": "PENDING_NUMERIC_EVIDENCE",
  "semantic_eliminations_per_frame": null,
  "implementable_eliminations_per_frame": null,
  "eligible_run_length_histogram": null,
  "open_static_items": [
    "GfxDrawIndexed helper audit incomplete",
    "first-unconsumed continuation address proof",
    "non-recursive trampoline bytes"
  ],
  "note": "Unconditional static GO is not expected while Gate 0 remains PENDING."
}
```

Human summary: prefix-batch loop-head model is structurally plausible; helper audit and Gate 3b continuation proof remain open before mutation PR.
