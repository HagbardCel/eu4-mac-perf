# Harness operations

The shortest supported path from a checkout to a verified result, plus the
recovery path when evidence or a launch is missing. Current capability state is
in [status](status.md); the review remediation matrix is
[implementation](../plans/closed/harness-review-20261120/implementation.md).

## Ownership

- **DSH** owns an operator-started native `/goal` reconstruction experiment.
- **OpenCode** is the supported default for campaign-run work through
  `python3 -m automation.cli` and the reconstruction skill.
- Do not start a second scheduler, a replacement agent loop or a new
  capture/launch script. Use the existing entry points below.
- A campaign checker `pass` is offline qualification only. Independent PR
  review is the merge gate.

## First steps

```sh
python3.14 -m automation.python_runtime resolve
python3.14 -m automation.agent_tools eu4_session discover --query <session-or-case>
python3.14 -m automation.agent_tools eu4_replay diagnose \
  --frame <sealed-frame> --metal <result-dir> --replay-out <out>
```

`eu4_replay diagnose` and `eu4_replay independent` return a compact
(<=8192-byte) agent projection by default with the on-disk report locator. Use
`--full` only when you need the complete detail. A completed divergent replay is
a successful diagnostic (`ok=true`, `verdict=different`); only an
execution/evidence failure is a tool failure.

For an existing package:

```sh
python3.14 -m automation.agent_tools eu4_replay verify --case <case>
python3.14 -m automation.agent_tools eu4_replay case --case <case> --replay-out <out>
python3.14 -m automation.agent_tools eu4_replay frame --frame <frame> --replay-out <out>
```

`frame` is raw execution; `diagnose` analyzes existing Metal output;
`independent` executes and diagnoses. Use the narrowest one that answers the
question.

## Campaign context and resume

The launcher sends a verified `goal_text` (objective + workspace/HEAD/limits/
budget/next commands) as the initial `/goal` message:

```sh
dsh/scripts/run.sh --campaign <sidecar.json>
```

On reconnect, rebuild the same read-only facts without anchoring, probing or
locking:

```sh
python3.14 -m automation.dsh_bridge context --sidecar <sidecar.json>
```

A missing campaign binding is reported as such; do not invent a trusted
checker. `eu4_finalize_candidate` is the only completion path and requires a
fresh checker pass (and independent review when the sidecar requires it).

## Focused verification

```sh
python3.14 -m automation.test_runner run --profile offline \
  --module tests.test_gfx_diagnosis --module tests.test_agent_tools
python3.14 -m automation.test_runner status --id <id>
python3.14 -m automation.test_runner run --profile offline --label full
```

The offline profile cannot launch EU4. Live tests are explicit
(`--profile live --module ...`) and need a real case and bounded timeout.
Native suites now distinguish a missing prerequisite (skip) from an eligible
compiler failure (failure); see `automation/native_build.py` and the log under
`.local/native-builds/`.

## Evidence and publication

```sh
python3.14 -m automation.case_locator publish --case <case> --root <evidence-root>
python3.14 -m automation.case_locator locate --root <evidence-root> \
  --session-id <id> --seal-sha256 <digest>
python3.14 -m automation.case_locator inventory --root <evidence-root> \
  --offset 0 --limit 20
```

Publication copies regular files only, rejects symlinks, and re-checks the
staged inventory against the pre-copy inventory. A verified lookup that
truncates its page is `truncated_unverified`/`safe_to_reuse=false`, never a
success. Raw shader, texture and game bytes stay local; publish metadata and
hashes, not proprietary payloads.

## Recovery

| Situation | Action |
|---|---|
| Missing case payload | Keep the locator; report the member/digest and configured `EU4_EVIDENCE_ROOT` roots. Do not scan the home directory. |
| Interrupted capture | Inspect the host lease and owned process before retrying. Preserve the partial output. |
| Identical deterministic failure | Change the candidate, diagnostics or evidence. A second unchanged failure pauses as `no_progress`; do not spend another round on the same input. |
| Terminal shadow result | The owned launch is stopped and settings restored; optional ops traces may be absent and are reported as such. |
| Stale/foreign receipt | Fail closed. Re-run the producer for the current candidate/seal; never repair a receipt by editing it. |

## Leases and campaign deadlines

The host-wide lease serializes EU4 launches. Active campaigns add a mode,
a stop state and a deadline. The offline test profile refuses live launches.

## Event-1 UV2 diagnostic probe

The opt-in probe observes event 1, program 310, framebuffer 6 and texture 433.

```fish
set -l OUT "$HOME/eu4-uv2-capture"
python3.14 -m automation.gfx_frame capture --output "$OUT" --prefix-end 1 \
  --uv2-probe --uv2-probe-event 1 --uv2-probe-program 310 \
  --uv2-probe-framebuffer 6 --uv2-probe-texture 433 --seal
python3.14 -m automation.gfx_frame translate --frame "$OUT/frame" --out "$OUT/translated"
python3.14 -m automation.gfx_frame replay --frame "$OUT/frame" --shaders "$OUT/translated" \
  --out "$OUT/metal" --uv2-probe --uv2-probe-event 1 --uv2-probe-program 310 \
  --uv2-probe-framebuffer 6 --uv2-probe-texture 433 --rationale 'event-1 uv2'
python3.14 -m automation.gfx_uv2_probe compare --gl "$OUT/uv2-probe" \
  --metal "$OUT/metal/uv2-probe" --frame "$OUT/frame" --out "$OUT/uv2-probe"
```

Outputs: `$OUT/uv2-probe/{uv2-probe.json,uv2-probe.rgba32f,uv2-control.bgra}`,
`$OUT/metal/uv2-probe/uv2-probe.rgba32f`, `$OUT/metal/uv2-color.bgra` and
`$OUT/uv2-probe/comparison.json`.

For existing artifacts, run `python3.14 -m automation.gfx_uv2_probe localize
--frame <sealed-frame> --replay <producer-receipt-directory>
--renderer-sha256 <expected-executable-digest>` offline. The JSON report lists
the first observed difference and the earliest unresolved stage for each
residual and up to eight nearby RGB-match controls in the observed GL
color-change region. The supplied executable digest is checked against the
producer receipt. Source-closure verification belongs to the independent
checker. A historical receipt remains diagnostic with
`compile_mode_provenance=unverified`. A current v3 recipe must agree with
`replay.json`. A matching RGB8 checkpoint or isolated UV2 value cannot
verify contributing-fragment correspondence. [Event-1
evidence and operator request](../analysis/m7/event1-investigation.md#numerical-observation-coverage).

`comparison.json` separates the isolated numerical result
(`numerical_comparison_conclusive`, classified `conditional_numerical_comparison`)
from the always-false authenticated original-GL claim.
`fragment_attribution_verified` is always false for this probe: shader discard,
colour-mask and logic-op effects on fragment correspondence are not evaluated,
and event 1 is depth-tested. Missing or partial draw state is unverified; control RGB
equality and coverage alpha are not evidence.

Refused: non-RGB8, multisample, stencil/alpha, non-zero indexed `first` or
`base_vertex`, instancing, integer attributes, >16 active attributes, unknown
VBO offsets, non-indexed draws.
