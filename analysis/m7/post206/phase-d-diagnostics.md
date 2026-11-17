# Phase D — actionable first-divergence diagnostics

Status: the existing PR #206 diagnostic contract already supplied most
required fields. This phase adds the missing pixel-level location, the
copy-source bisection recommendation, engine identity, and a bounded
machine-readable projection. It does not add a new renderer or a second
qualification path.

## What now answers each question

| Question | Field / evidence |
| --- | --- |
| First observed pixel divergence | `first_divergence.pixel_location` and `differing_bounds`, plus final-pixel `statistics` |
| Earliest observed checkpoint divergence | `first_divergence.kind=intermediate`, `event`, `target`, `resource`, `statistics`; `checkpoint_validation` |
| First unqualified frame | campaign `first_unqualified_frame` and `first_unqualified_frame_identity` |
| Terminal failure | campaign `terminal_failure`, which outranks pixels |
| Operation / draw index | `event`, `producer_event`, `producer_entry`, `producer_caller_rva`, `producer_program` |
| Rendering state | `first_divergence.state` and `inputs` |
| Shader identity | `program`, `producer_program`, `producer_entry`, per-frame `shader_report_sha256` |
| Resource identity | `resource`, `target`, `render_target`, `dependent_resources`, `prior_producers` |
| Unresolved causal interval | `first_divergence.unresolved_interval` |
| Recommended next checkpoint | `first_divergence.next_checkpoint` |
| Compact agent package | `agent-diagnosis.json` (`gfx-agent-diagnosis/v1`) |

## Authentic example (frame 1, present index 615)

```json
{
  "kind": "intermediate",
  "event": 284,
  "target": "0",
  "resource": "fb:0",
  "operation": "glCopyTexSubImage2D",
  "producer_event": 283,
  "producer_entry": "_GfxDraw",
  "producer_program": 346,
  "pixel_location": {"x": 0, "y": 0, "offset": 0},
  "differing_bounds": {"x": 0, "y": 0, "width": 1728, "height": 1117},
  "unresolved_interval": {"start_event": 13, "end_event": 284},
  "next_checkpoint": {
    "event": 148,
    "target": "0",
    "requires_capture": true,
    "reason": "the copy source already differs; capture a default-framebuffer checkpoint near the midpoint of the unresolved interval to bisect the origin"
  },
  "confidence": "confirmed",
  "root_cause": "not_established"
}
```

The report distinguishes the earliest **observed** difference from a proven
cause. The captured copy source is authoritative and differs; the copy
execution is not established as the cause, and the GL/GL control proves it is
exact.

## Progressive strategy

1. Frame-level result: classify the three frames; stop at the first
   non-qualified frame by default or continue only under an explicit policy.
2. Pass/draw range: the first divergent checkpoint is the copy source at event
   284; the last matching authoritative checkpoint is event 13, so the
   unresolved interval is 13–284.
3. State, shaders and resources: report the producer draw (283, `_GfxDraw`,
   program 346), its captured inputs (`tex:428`) and the unsupported states.
4. Targeted checkpoints: recommend a default-framebuffer checkpoint at event
   148 (midpoint), with `requires_capture=true`; do not promise an event that
   the current evidence cannot compare.
5. Compact package: write `agent-diagnosis.json` beside the full
   `diagnostic-report.json`.

No GPU readback after every operation is introduced. The checkpoint set is the
existing deterministic first-consumer plan plus the captured copy source; the
compact package is a projection of the full report.

## Compact package

For the authentic frame 1 report:

| Artifact | Size |
| --- | --- |
| `diagnostic-report.json` | 3,007,849 bytes |
| `agent-diagnosis.json` | 6,201 bytes |

The projection keeps only the frame identity, execution status, first
divergence, up to 16 inputs, up to 32 checkpoints, final statistics,
checkpoint validation, artifact paths and `next_action`. It deliberately omits
the dependency graph and raw event data; the full report remains the
authoritative evidence.

## Regression coverage

| Case | Regression |
| --- | --- |
| Early injected fault (frame 2, event 1) | `test_m7_continuous_replay.ControlledFrameFaultTests.test_diagnosis_reports_the_selected_frame_and_fault_event` |
| Late injected fault (frame 2, event 291) | `test_frame_scoped_fault_only_changes_the_selected_frame` |
| Cross-frame fault and history | `test_m7_continuous_replay.ContinuousReplayHistoryTests.test_frame_n_output_is_sampled_from_metal_in_frame_n_plus_one`; authentic history `status=ok`, sequence 3 |
| Missing/invalid evidence | `test_m7_continuous_supervisor.DiagnosticEvidenceTests` (missing/invalid pixel statistics, incomplete/invalid checkpoints) |
| Pixel coordinate/bounds | `test_m7_pixel_diagnostics.PixelLocationTests` |
| Compact projection | `test_m7_diagnosis.CompactAgentDiagnosisTests` |
| Copy-source recommendation | authentic `diagnose` output above; `test_m7_diagnosis` contract tests |

## Remaining gap

The event-148 checkpoint cannot be produced from the existing capture: the
current capture records the copy source and the first-consumer checkpoints, but
not an arbitrary default-framebuffer image at a chosen event. The actionable
next step is a bounded capture option for `--default-checkpoint-event 148`
(or an equivalent capture-side feature), followed by one re-capture. This is
deliberately not faked from the final reference.
