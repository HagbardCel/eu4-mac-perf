# Structured EU4 reconstruction tools

One Python core serves every agent surface. The CLI prints exactly one JSON
envelope; the DSH/OpenCode adapters are thin argument mappers.

Artifact ownership (which stage owns a capture, graph, replay, live comparison
or offline case) is recorded in [current project status](status.md#artifact-ownership).

```json
{
  "schema": "eu4-agent-tool/v2",
  "tool": "eu4_diff_inspect",
  "ok": true,
  "status": "ok",
  "reason": "",
  "result": {},
  "outcome": {
    "execution": "ok",
    "diagnostic": "complete",
    "diagnostic_complete": true,
    "verdict": "different",
    "evidence": "valid",
    "qualified": false,
    "failure_kind": null,
    "failure_class": "comparison"
  },
  "failure": {"kind": null, "class": "comparison", "detail": ""},
  "runtime": {
    "executable": "/absolute/python3.14",
    "configured": "/absolute/python3.14",
    "mismatch": false,
    "numpy": {"required": true, "available": true, "version": "2.x"}
  }
}
```

Exit codes: `0` ok/pass/completed, `2` denied/inconclusive/not-found/
unavailable, `3` usage or unexpected error. A refused launch is a structured
result, not a traceback.

## Outcome semantics

`eu4-agent-tool/v2` separates five questions that used to collapse into `ok`:

| Field | Values | Meaning |
|---|---|---|
| `outcome.execution` | `ok`, `error` | did the adapter and Python core run? |
| `outcome.diagnostic` | `complete`, `partial`, `incomplete`, `unavailable`, `refused`, `invalid`, `failed`, `not_applicable` | did the requested diagnostic operation complete? |
| `outcome.verdict` | `different`, `equal`, `unverified`, a GPU verdict, `null` | the comparison/rendering conclusion, if any |
| `outcome.evidence` | `valid`, `invalid`, `unverified`, `null` | is the evidence the operation used trustworthy? |
| `outcome.qualified` | `true`, `false`, `null` | the separate qualification outcome |
| `outcome.failure_kind` | `null`, `execution_timeout`, `missing_credential`, `graphics_unavailable`, `dependency_unavailable`, `evidence_invalid`, `evidence_missing`, `policy_refusal`, `tool_failure` | the operational failure class, separate from a comparison difference |

A completed comparison that finds different pixels has no `failure_kind`; a
timeout, missing credential, unavailable graphics capability, missing
dependency, invalid evidence and genuine mismatch are distinct outcomes. A
shader or replay correctness failure is a tool failure, not a GPU-capability
failure, and invalid evidence wins over generic dependency wording. A nested
invalid verification/diagnosis downgrades the envelope. No failure class can be
promoted to a successful qualification.

`ok` is true only when execution succeeded and the diagnostic is complete or
explicitly bounded (`partial`). Consequences:

- a comparison that runs and finds different pixels is **ok** with
  `verdict: "different"` — not a tool failure;
- a comparison that cannot run because NumPy is missing is **not ok**, with
  `diagnostic: "unavailable"` and an actionable reason naming the interpreter;
- a bounded frame page is ok with explicit `truncated`/`next_offset`, not a
  silent cap;
- an unknown GPU-health verdict is never reported as established healthy
  execution.

v1 consumers can keep reading `ok`, `status`, `result`; the schema version was
bumped because `ok` is now false for incomplete diagnostics that v1 reported as
successful. The result payloads remain backward compatible; `first_pair` and
the raw recorded comparison are available with `--full`.

Every envelope also carries `runtime`, computed from the process that actually
executed the tool. If `EU4_DSH_PYTHON` names a different interpreter, the
envelope says so with `mismatch: true`.

## Interpreter and dependency policy

`dsh/scripts/run.sh` and `dsh/scripts/preflight.sh` resolve one absolute Python
with NumPy and export it as `EU4_DSH_PYTHON`; the profile passes that value to
the tools plugin. A virtualenv entry point such as `.venv/bin/python3` keeps its
path: it is not dereferenced to the base interpreter, and the selected
interpreter is always the one probed. Dependencies are imported, not merely
located, so a broken module is unusable even when its specification exists.
The adapter never falls back to a bare `python3`: a missing or unusable setting
is a structured error. To check a checkout:

```sh
python3 -m automation.python_runtime resolve
EU4_DSH_PYTHON=/absolute/venv/bin/python3 python3 -m automation.python_runtime resolve
```

`resolve` is authoritative: an explicitly requested interpreter is never
replaced by another installation when NumPy is missing, and the error names
both the interpreter and the missing dependency. The runtime block reports the
executing environment (`environment`, `virtualenv`), the configured path and
environment, and a `mismatch` that compares environments rather than resolved
binary paths — two virtualenvs sharing a base binary are still different
environments.

## Tools

### `eu4_re_query`

Query existing Ghidra/corpus knowledge before disassembling. Requires the local
corpus (or `EU4_RE_INDEX` / `EU4_RE_EXPORT`).

```sh
python3 -m automation.agent_tools eu4_re_query resolve SShaderOpenGL --limit 3
python3 -m automation.agent_tools eu4_re_query function 0x15eb892 --max-bytes 8000
python3 -m automation.agent_tools eu4_re_query callers 0x15eb892
python3 -m automation.agent_tools eu4_re_query xrefs 0x15eb892
python3 -m automation.agent_tools eu4_re_query abi 0x15eb892
python3 -m automation.agent_tools eu4_re_query struct mesh-draw
python3 -m automation.agent_tools eu4_re_query provenance
```

`xrefs` is explicitly `partial`: the offline extracts contain call edges, not
data references. `abi` is `unavailable` unless the function is in the sealed
renderer snapshot; it never guesses a prototype.

### `eu4_research`

Read the sealed renderer snapshot when the indexed corpus is not enough:
machine instructions, decompiler output and paginated static cross-references
for one function. `eu4_re_query` stays the fast indexed entry point for
symbols, relationships, ABI and layouts; `eu4_research` adds the instruction
and decompilation evidence from the frozen snapshot. It never launches Ghidra,
rebuilds the corpus or writes evidence.

```sh
python3 -m automation.agent_tools eu4_research status
python3 -m automation.agent_tools eu4_research resolve InternalSetTextureAndSamplerState
python3 -m automation.agent_tools eu4_research function 0x15ecce2 --view static \
  --limit 40 --max-bytes 16384 --static-offset 0
python3 -m automation.agent_tools eu4_research function 0x15efec7 --view decompiled \
  --decompile-offset 4000 --max-bytes 16384
python3 -m automation.agent_tools eu4_research xrefs 0x15ecce2 \
  --incoming-offset 0 --outgoing-offset 10 --callsite-offset 0
```

The research document's own status is preserved: `ok`, `unavailable` (missing
corpus/index/package/callsites), `not_found`, `not_in_snapshot`,
`identity_error` (stale index or invalid package/receipt) and `ambiguous`
(multiple or truncated matches). A missing corpus is never an empty successful
result. `view` selects `static`, `decompiled` or `all`; the independent
`static_offset`/`decompile_offset` (functions) and `incoming_offset`/
`outgoing_offset`/`callsite_offset` (xrefs) continue a bounded page, and
`max_bytes` keeps the document within its 1 KiB–256 KiB response budget.

### `eu4_session`

```sh
python3 -m automation.agent_tools eu4_session list
python3 -m automation.agent_tools eu4_session list --include-history --limit 20
python3 -m automation.agent_tools eu4_session list --status stale
python3 -m automation.agent_tools eu4_session inspect --run <session-dir>
python3 -m automation.agent_tools eu4_session terminate --run <session-dir>
python3 -m automation.agent_tools eu4_session lease
python3 -m automation.agent_tools eu4_session discover --limit 10
```

`list` is active-only by default and returns compact rows
(`session_id`, `run_dir`, workflow/profile, timestamps, classification) plus
explicit `counts` (`active`, `closed`, `stale`, `total`) and
`returned`/`truncated`/`next_offset`. Closed and stale records are opt-in with
`--include-history` or `--status`. A held marker lock is `active`, a recorded
terminal status is `closed`, and a marker whose owner is gone without a
terminal state is `stale`.

`discover` is read-only historical campaign discovery (see below). `lease`
reports `free`/`busy`/`manual`, the holder's worktree/pid and any manually
started EU4 PID without acquiring the lease. `terminate` signals only the
process identities recorded in that session, after re-verifying
pid/start-time/executable. It never enumerates `eu4` processes.

### `eu4_capture`

```sh
python3 -m automation.agent_tools eu4_capture health --run <session-dir>
python3 -m automation.agent_tools eu4_capture frames --run <session-dir> --frame 74
python3 -m automation.agent_tools eu4_capture frames --run <session-dir> --frame 74 --around 2 --role reference
python3 -m automation.agent_tools eu4_capture frames --run <session-dir> --frame 74 --full
python3 -m automation.agent_tools eu4_capture request --run <session-dir> --role candidate --frame 42
python3 -m automation.agent_tools eu4_capture probe --checkpoint after-present \
  --output <dir> --campaign-run-root <authorized-root>
python3 -m automation.agent_tools eu4_capture gl-control --run <capture-root>
python3 -m automation.agent_tools eu4_capture gl-control-qualify \
  --live-image <sealed-checkpoint.bgra> \
  --width 1728 --height 1117 --frame-dir <sealed-frame> \
  --checkpoint-event 15 --draw-event 14 \
  --renderer <original-api-control-runner> --control-out <empty-dir>
python3 -m automation.agent_tools eu4_capture gl-control-qualify \
  --live-image <sealed-checkpoint.bgra> --control-image <control.bgra> \
  --width 1728 --height 1117 --comparison-only
```

`frames` applies exact `--frame` filtering (repeatable), optional `--role`
filtering, bounded `--around` neighbours, `--offset`/`--limit` pagination and a
compact record by default. It reports `matched`, `returned`, `omitted`,
`truncated`, `next_offset`, per-role observation counts, available frame ranges
and `missing_frames`. A requested frame that exists nowhere in the selected
streams is `incomplete`, not an empty success. `--full` restores raw observation
records for deeper investigation.

`health` reconciles the persisted session health with a fresh scan (including
both role directories): each source is reported with its path and verdict, the
`agreement` is explicit (`agree`, `disagree`, `persisted_only`, `scan_only`),
and a disagreement is preserved. An `unknown` verdict is never presented as
healthy execution. On a campaign root with several sessions it returns one
bounded health row per session instead of silently choosing one.

`gl-control` runs the same-backend GL control for every captured copy source /
destination pair (`--copy-source` capture). It compares the GL default
framebuffer image with the GL destination texture under the declared channel
normalization and requires zero differing pixels. It is scoped to the captured
transfer/readback path; it is not a full-frame GL replay. A mismatch is
`invalid` evidence, never a qualified result.

`gl-control-qualify` is the selected-draw original-API gate. It reports three
separate outcomes: exact BGRA comparison, authenticated sealed-observation
qualification, and permission for causal Metal attribution.
`--live-image` must be the sealed post-draw checkpoint blob; the same frame's
event stream must also contain the pre-draw checkpoint at `--draw-event`, which
becomes the colour seed. Qualification requires the frame seal to validate; the
checkpoint identity to come from the seal-verified event stream's own
`default_checkpoint` record (not a mutable request sidecar); the live image to
be that exact sealed blob and digest; and a completed `opengl` control renderer
executed by the qualifier with a fresh challenge nonce.

The qualifier derives the mandatory drawable and pre-draw state from the seal
itself, never from caller JSON: `GL_SAMPLES`, `GL_SAMPLE_BUFFERS`, depth/
stencil/colour bit depths and the actual extent from the captured `gl.drawable`
record; `GL_FRAMEBUFFER_SRGB`, `GL_DITHER` and `GL_MULTISAMPLE`; and depth/
stencil contents, either as captured seeds or as a provably complete clear history
(full extent, recorded clear values, enabled write masks, no intervening
same-target writer). The pre-draw colour image is mandatory: a clear is not a
substitute for it.
`--drawable` and `--prestate` are refused. A missing or unproven observable is
an explicit `unqualified` or `unsupported_state`, even when the two images are
byte-identical.

Provided-input isolation is structural. The renderer receives a separate,
content-addressed replay-input package (`replay-input/replay-input.json` plus
`blobs/<sha256>.*`) containing only authorised pre-draw state, shaders,
uniforms, typed buffers, indices, captured texture blocks and sampler/render
state. It never receives the frame directory, the live image path, the live
digest or a copy of the target checkpoint. The comparison reference stays with
the qualifier. `--control-out` must not be inside the sealed capture root (the frame
directory's parent) and must not be an ancestor of it; an output directory that
overlaps that tree in either direction is refused as `unsafe_output_directory`,
because a renderer can walk its working directory and the package ancestors. The package is a closed set: undeclared files,
nested directories and symlinks are refused, and a symlinked receipt or
artifact is not accepted as evidence. Pre-created `replay-input`/`renderer-run`
symlinks and any that resolve inside the capture tree are refused, and the
renderer inherits a scrubbed environment (`PWD`/`OLDPWD` removed, `HOME` and
`TMPDIR` pointed at its run directory).

This is isolation of the *provided input surface*. Filesystem sandboxing of an
arbitrary process is not available on the supported host (`sandbox-exec` is
denied), so the authority registry remains the trust anchor: it pins the exact
reviewed renderer. A deliberately malicious pinned renderer that searches the
host filesystem for the sealed frame is outside the boundary the gate can
prove; the negative test exercises a renderer that searches its working
directory, ancestors and package and finds no reference.

The renderer must write `result.bgra` plus a signed `receipt.json` under
`--control-out/renderer-run`. The receipt binds the renderer digest, its
registry-declared component digests, the challenge nonce, the frame/stream/
draw/checkpoint identity, the effective input-package digest, the output image
digest/dimensions/format/origin, the queried target drawable properties and a
completed execution status, and it is signed with the Ed25519 key registered for
that renderer in
[`automation/gfx_gl_control_authorities.py`](https://github.com/HagbardCel/eu4-mac-perf/blob/main/automation/gfx_gl_control_authorities.py).
The signed payload is canonical JSON (sorted keys, compact separators, UTF-8,
no NaN) of the receipt object with `signature` removed; the `signature` object
names that canonicalisation. Component entries carry the path of the file whose
digest they claim, and the qualifier re-hashes every component file and the
executed renderer during `attribution()`.
Adding an entry is a reviewed code change, not a caller-supplied JSON file.
The registry currently pins one reviewed original-OpenGL renderer (exact
reviewed head `283c3e12`). Without a registry entry the run is `self-attested`
and `metal_blame_allowed` stays false, however exact the pixels are; a pinned
entry does not replace the separate drawable-equivalence and exact-comparison
requirements.

The qualifier re-verifies the package before and after execution, hashes the
sealed live blob and reads it once, and uses the authenticated bytes for the
comparison, so a renderer cannot change what is being qualified.
`attribution()` re-authenticates the seal, re-derives the evidence, re-verifies
the package and the signature, and recomputes exact four-channel equality
instead of trusting a boolean. An exact image from a different seal is
explicitly `unqualified`; `--comparison-only` compares two files and can never
qualify. Supplying `--renderer` executes that file with the caller's privileges;
the checked-in registry entry is the explicit trust decision, and the qualifier
does not sandbox the renderer.

`probe` is a pass-through to the existing live launch path and still requires an
operator ledger.

### `eu4_diff_run`

```sh
python3 -m automation.agent_tools eu4_diff_run \
  --scenario .local/infra/dual-1/scenario.json \
  --run-root .local/infra/dual-1 \
  --ledger .local/infra/dual-1/authority/dual-6.json \
  --shader-cache /path/to/shader-cache.json \
  --mode lockstep --max-frames 20
```

The core claims two launches atomically, shares one host-wide lease, streams and
aligns observations, applies the health gate, stops on the first confirmed
failure, preserves evidence, and terminates only its own processes. A completed
campaign is a completed tool operation even when its diagnostic classification
is `incomplete`; qualification is reported separately.

### `eu4_diff_inspect`

```sh
python3 -m automation.agent_tools eu4_diff_inspect --run <campaign-root>
python3 -m automation.agent_tools eu4_diff_inspect --run <session-dir>
python3 -m automation.agent_tools eu4_diff_inspect --run <session-dir> --build-case
python3 -m automation.agent_tools eu4_diff_inspect --run <session-dir> \
  --temporal --reference-frame 74 --candidate-frame 74
```

`--run` accepts a session directory or a campaign root. A campaign root with one
session is inspected; with several sessions it returns a bounded list of session
identities and outcomes and never auto-selects one. The compact result contains:

- the recorded and re-derived classification, with the reasons and any change
  (recorded `rendering_difference` on an unverified static scene re-derives to
  `incomplete`);
- reconciled GPU health with source provenance and agreement;
- correspondence status/evidence and staticity evidence;
- the comparison verdict and first meaningful discrepancy (frame identities,
  game day, update identity, caller signature, draw count, digests, statistics);
- missing evidence;
- category-specific next steps (process/GPU/alignment/staticity/renderer), so a
  per-draw capture is not recommended when the observations already identify a
  different problem;
- qualification.

`--temporal` adds the reusable consecutive/cross-process frame diagnostics
described below. `--full` restores the raw recorded comparison and first pair.

### Compact case inspection and verification

`eu4_case_inspect` and `eu4_replay verify` return a bounded
`gfx-agent-case/v1` projection by default. It carries the case and capture
identity, verification status, qualification axes, first divergence, selected
checkpoint statistics, the next recommended checkpoint, artifact names/sizes
and the next action. The full manifest, dependency graph, command stream and
diagnostic report stay on disk. The projection is capped at 8,192 emitted bytes
and reports `truncated`, `output_cap_bytes`, `output_bytes_before_cap` and
`truncated_fields` when the cap is reached. If the essential identity,
verification, first-divergence, interval or checkpoint fields cannot fit, the
tool returns an explicit bounded `output_cap_exceeded` error instead of
dropping them. The cap is measured on the exact
emitted UTF-8 bytes (including the trailing newline), not JSON character
counts. Essential identity, verification, first-divergence, unresolved-interval,
recommended-checkpoint and artifact references are never removed. Pass
`--full` only when a specific missing field requires the complete report view.

```sh
python3 -m automation.agent_tools eu4_case_inspect --case <case>
python3 -m automation.agent_tools eu4_case_inspect --case <case> --full
python3 -m automation.agent_tools eu4_replay verify --case <case>
```

### Event evidence inspection

`eu4_case_inspect --event <index>` projects one captured event without capture,
replay, launch or cache. `--frame` selects a sequence frame;
`--metal`/`--shaders` name an existing replay/translation.

```sh
python3 -m automation.agent_tools eu4_case_inspect \
  --case <case-or-sealed-frame> --event 1 --pixel 512,341
```
It returns a bounded (8,192-byte) record with identity, program/resource
references, exact FP32 bits, bindings/state, observations, omission counts and
pixel differences. Metal correspondence requires the replay's request/receipt
to bind the capture; `--full` adds the raw event.

### Baseline reuse

`automation.case_baseline` records one identity for an existing case only after
`gfx_replay_case.verify()` passes (its single payload pass re-hashes every
declared file from disk), the replay result/readback is complete with
`stage=complete`, and `replay/producer-receipt.json` binds the executable/build
that produced the readback. Reuse is bound to the verified packaged `replay/`
directory; an external `--replay-dir` is rejected. Legacy cases without a
producer receipt remain historical evidence but are rejected for reuse. The identity covers the frame
seal, diagnostic report, graph, replay result/readback, translated shader
inputs, the renderer include closure (including `eu4_gfx_replay_semantics.h`),
the replay binary/source, comparison policy and platform. It is not a result
cache: a mismatch or an unverified current case forces a recomputation.

```sh
python3 -m automation.case_baseline record --case <case> --baseline <out>.json
python3 -m automation.case_baseline check --case <case> --baseline <out>.json
```

### Test profiles

Routine tools run under the offline profile; see
[M7 offline and live test profiles](m7-test-profiles.md) for the gate, the
launch guards and the explicit live opt-in.

### `eu4_replay`

```sh
# v1 single-frame and v2 ordered-prefix Strategy B cases are recognized by the
# same schema dispatch; omit --out to verify, pass it to replay.
python3 -m automation.agent_tools eu4_replay verify --case <case-dir>
python3 -m automation.agent_tools eu4_replay case --case <case-dir> \
  --out <replay-dir>

# Run the Metal host on a stored frame, then analyse the result.
python3 -m automation.agent_tools eu4_replay independent --frame <frame-dir> \
  --shaders <shader-dir> --out <replay-dir> --rationale <rationale.json>

# Diagnose already-produced Metal evidence without executing Metal again.
python3 -m automation.agent_tools eu4_replay diagnose --frame <frame-dir> \
  --metal <metal-result-dir> --out <diagnosis-dir> --require-seal

# Bounded M7 ordered sequence: one persistent Metal session, one translation
# per frame, explicit input/case/time budgets.
python3 -m automation.agent_tools eu4_replay sequence \
  --frame-dir <frame-1> --frame-dir <frame-2> --frame-dir <frame-3> \
  --shaders <fallback-shader-dir> --out <campaign-dir> \
  --max-frames 3 --case-max-bytes 536870912 --frame-timeout 180 \
  --require-engine-identity

# Controlled divergence: apply the sampler fault only to frame 2 event 1.
# This is a diagnostic experiment and is never qualified.
python3 -m automation.agent_tools eu4_replay sequence \
  --frame-dir <frame-1> --frame-dir <frame-2> --frame-dir <frame-3> \
  --shaders <fallback-shader-dir> --out <campaign-dir> --max-frames 3 \
  --continue-on-difference --fault-frame 2 --fault-event 1 \
  --sampler-shift 1 --sampler-shift-min 0

# Existing selected-event single-frame replay.
python3 -m automation.agent_tools eu4_replay frame --frame <trace-dir> \
  --shaders <shader-dir> --out <dir> --only-event 92
```

The case path is fully offline. A missing NumPy is `unavailable`, never a
successful diagnostic; a successful comparison with a difference verdict is
`ok` with `verdict: "different"`. A v2 case whose reproduction is explicitly
incomplete, or whose verification finds digest/identity/path problems, is
`incomplete` or `invalid` and is never replayed or marked qualified. An
unknown `gfx-strategy-b-case/*` version is `invalid`; only the recognized
legacy image-comparison schema reaches the older comparator.

`independent` and `diagnose` accept an original sealed frame or a copy bound
by an adjacent `trusted-evidence.json`. They verify the record, the original
capture-directory binding and the frame seal. The resulting capture validity
is separate from Metal identity, checkpoint coverage and exact-prefix
qualification. Use `result.axes.capture_validity` and `result.axes.gpu_execution`
to inspect those axes of a replay diagnosis. A valid reference can accompany an
unverified Metal comparison.

The standalone `python3 -m automation.gfx_independent_replay` keeps the Metal
host result in `outcome.execution`. Read `diagnostic_outcome` for the canonical
diagnosis, where `execution` describes whether the tool returned a result.
The standalone `diagnose` subcommand exposes those same fields under
`diagnostic_outcome` and retains `outcome` as an alias. The `eu4_replay` tool
envelope continues to report tool execution in `outcome.execution`.

For a shell fallback, check the subprocess exit status and parse its JSON
envelope before using the comparison. The agent CLI can return zero for a
completed difference. Inspect `outcome.diagnostic`, `outcome.evidence`,
`outcome.verdict` and `outcome.qualified`. A framebuffer on disk cannot validate
an invalid or unverified comparison.

`--out` after `eu4_replay` names the replay/diagnosis output directory.
`--envelope-out` is the separate JSON-envelope file and may appear anywhere:

```sh
python3 -m automation.agent_tools eu4_replay case --case <case> \
  --out <replay-dir> --envelope-out <envelope.json>
```

A missing or malformed `--policy` is refused; it never silently falls back to
the default policy. The campaign and diagnosis evidence record the policy
path, content SHA-256 and whether it was explicit or the default. A campaign
with a terminal GPU/resource-history/capture failure is `failed`/`invalid`
even when an earlier frame reported a rendering difference.

`eu4_replay sequence` requires each replay result to carry exactly the next
expected sequence number; duplicate, skipped, reversed or out-of-range results
stop the campaign with a structured `stop_reason: "sequence_order"` before
diagnosis. `qualified: true` requires every input frame to have been executed
and diagnosed. The result separates:

- `first_pixel_divergent_frame` — verified divergent final pixels;
- `first_checkpoint_divergent_frame` — a confirmed authoritative intermediate
  checkpoint difference;
- `first_unqualified_frame` — the first frame not classified `qualified_match`;
- `terminal_failure` — the first execution/history/invalid-capture failure;
- `first_difference` — the legacy earliest classified non-equivalence, which
  may be an `unsupported_state`/semantic mismatch and is not necessarily a
  pixel difference.

`--require-engine-identity` refuses a sequence whose frames do not carry
consecutive engine `present_index`/`scene_index` values. A skipped or partial
identity is `engine_frame_gap` / `engine_identity_partial`; an old capture with
no identity reports `engine_identity.status=unavailable` rather than inferring
one. `--fault-frame`/`--fault-event` scope the existing sampler fault to one
frame and event; the campaign records `fault_injection` and can never be
`qualified`.

`diagnostic_complete` is evidence-based: every processed frame must have
executed the diagnosis and produced its required evidence. A populated
`diagnostic_report` path is only a locator; a Metal execution failure row
reports `diagnostic_report: null` and `diagnostic_completed: false`.

The aggregate `verdict` is derived from confirmed evidence across all trusted,
diagnosed frames, not only the earliest classified non-equivalence. Only a row
with `diagnostic_completed=true` can contribute evidence; completion requires a
usable final-pixel statistics object (positive pixel count and a consistent
mismatched-pixel count) and an authoritative `ok` checkpoint validation. A
later verified final-pixel or authoritative checkpoint difference therefore
yields `different` even when an earlier frame only observed `unsupported_state`;
terminal failures still override the verdict without erasing the earlier
observations. Evidence from a terminal, invalid-provenance or incompletely
diagnosed frame is never promoted into `first_pixel_divergent_frame` or
`first_checkpoint_divergent_frame`.

Portable v2 case replay (`eu4_replay case --replay-out`) succeeds only when the
campaign is `complete`, every declared frame was executed and diagnosed, the
last frame is the declared target, and no terminal GPU/history/provenance/
identity/infrastructure failure occurred. Pixel equivalence is not required; a
fully executed divergent campaign is a valid reproduction. A premature stop
raises a structured `sequence_replay` error and never reuses the last available
frame's diagnosis.

### `eu4_case_inspect`

```sh
python3 -m automation.agent_tools eu4_case_inspect --case <case-dir>
```

## Temporal frame diagnostics

The canary's decisive measurement is now a library function in
[`automation/gfx_frame_compare.py`](../automation/gfx_frame_compare.py), exposed
through `eu4_diff_inspect --temporal`. It compares, using the existing decoder,
origin normalization, scale handling, comparison metrics and image writer:

- reference frame N vs N+1 (same process, consecutive);
- candidate frame M vs M+1 (same process, consecutive);
- reference N vs candidate M (cross-process, normalized to the reference
  extent).

Each pair reports mismatched pixels/fraction, maximum channel error, mean
absolute error, per-region statistics, difference bounding box and the temporal
frame identities/digests. `overlap` intersects the cross-process mask with each
within-process mask: `cross_also_temporal_fraction` is the share of
cross-process differing pixels that also change between consecutive frames of
that process. Difference PNGs are written only with `--temporal-artifacts` and
always use the same pair and normalized arrays as their statistics.

Completion is explicit. Every requested comparison is reported as `completed`,
`skipped` (the documented pair bound) or `unavailable` (missing frame or
normalization failure), with separate cross-process and temporal-variability
flags. Successful within-process pairs never imply that a requested
cross-process pair ran. If a requested cross-process or consecutive comparison
cannot be performed, the top-level status is `incomplete` and the useful
statistics that did run are retained; pair-bound truncation is reported
separately. `eu4_diff_inspect` propagates an incomplete/partial temporal result
instead of claiming a complete diagnostic.

Cross-process normalization follows the scenario's
`comparison.allow_scale_normalization` policy, records the original extents,
scale and method, and applies exact-policy semantics: equality after resampling
is `unverified`, not bit-identical, while a genuine difference remains a
difference. Candidate-temporal pairs remain in candidate coordinates.

The temporal window is bounded (4 consecutive frames per role, 12 requested
comparisons). An explicit request beyond that bound is refused with the bounds
in the response; an explicitly requested missing index is reported as missing,
never silently replaced by a different pair. Only the selected frames and their
required neighbours are decoded.

Without explicit indices, the selector prefers the recorded pair only when its
payload files are present and plausibly sized; otherwise it inspects candidate
starts in order (bounded metadata scan) and probes a window only when metadata
and cheap payload availability make it worth decoding. The decode-attempt bound
is separate from the metadata scan, so many metadata-only or missing-payload
frames cannot exhaust the decode allowance. `selected.source`,
`selected.fallback`, `selected.probes`, `selected.candidates_considered`,
`selected.search_exhausted` and `fallback_used` are reported; an exhausted
search is explicit rather than implying that no usable window exists. A payload is decoded and, when its
metadata declares a digest, verified before comparison; a digest mismatch or
undecodable payload is reported in `invalid` and cannot produce completed
statistics. Metadata enumeration never hashes unselected payloads.

This recovers the recorded Experiment A findings without a private script:

| Pair | Mismatched fraction |
|---|---|
| reference 74 → 75 | 3.77 % |
| reference 75 → 76 | 79.81 % |
| candidate 74 → 75 | 6.43 % |
| reference 74 vs candidate 74 | 1.35 % |
| cross-process pixels also in reference 74 → 75 | 92.0 % |

Downscaled preview hashes cannot prove staticity: the tests include a case
where the preview is bit-identical while the full-resolution frame differs.

## Evidence discovery

`eu4_session discover` scans an explicitly configured root list, never the whole
filesystem:

```sh
EU4_EVIDENCE_ROOT=/path/to/worktree/.local:/path/to/other/.local \
  python3 -m automation.agent_tools eu4_session discover --limit 10
python3 -m automation.agent_tools eu4_session discover \
  --evidence-root /path/to/worktree/.local --query a6 --classification incomplete
```

Discovery is read-only, bounded (root, root/campaign, root/campaign/session-*),
and each row carries the original `result.json`/`case.json` paths and SHA-256
digests, the recorded classification and its provenance, health, stop reason and
qualification. Nothing is copied, moved or mutated. A local `.local` default
keeps the result repository-scoped; set `EU4_EVIDENCE_ROOT` to share evidence
across worktrees. The index does not re-derive classifications: `eu4_diff_inspect`
does that for the session you select.

## Focused test execution

[`automation/test_runner.py`](../automation/test_runner.py) runs focused
`unittest` modules, writes the complete log under `.local/test-runs/`, and
returns a compact machine-readable summary. It preserves the child exit code.
An identical suite already running is returned as **pending** (`ok: false`,
CLI exit 2) — never as a completed pass. A process that exits successfully
while executing zero tests is **incomplete**, not a pass, and the original
child exit code is preserved separately. A base/candidate comparison requires
the same executed selection (different checkout directories are allowed),
completed runs and a non-zero test count; otherwise the verdict is
**`incomparable`** with the reasons, not `no_new_failures`.

```sh
python3 -m automation.test_runner run --module tests.test_agent_tools
python3 -m automation.test_runner run --module tests.test_agent_tools \
  --module tests.test_gfx_case --label after-fix
python3 -m automation.test_runner status --id <run-id>
python3 -m automation.test_runner list --limit 10
python3 -m automation.test_runner compare --base <id|record.json> --candidate <id|record.json>
```

## DSH session efficiency

[`automation/dsh_efficiency.py`](../automation/dsh_efficiency.py) analyzes one
existing DSH JSONL export (plain or `.jsonl.zstd`): calls by tool, failure rate,
repeated calls, duplicate test invocations, oversized/truncated results, long
jobs, tool wait, token usage with cache reads separated, EU4 launches, human
interventions and goal outcome. It is a one-shot read-only report, not a
monitoring service. `--turn N` isolates one turn; the M7.0 canary investigation
is turn 1.

```sh
python3 -m automation.dsh_efficiency analyze \
  ~/.local/share/eu4-dsh/sessions/.../session.v4.jsonl.zstd --turn 1
```

The committed fixture [tests/fixtures/dsh/efficiency-session.jsonl](../tests/fixtures/dsh/efficiency-session.jsonl)
is synthetic and sanitized: no credentials, machine paths or private content.

## DSH plugin

`dsh/eu4-tools-plugin` (`@eu4/dsh-tools`) registers the eight names as native
DSH tools with typed JSON schemas and spawns the Python core. It has no runtime
dependency on DSH packages (raw JSON schema), so it can be linked into a
profile:

```sh
dsh plugin --profile <profile> add link:$PWD/dsh/eu4-tools-plugin
```

The `eu4-web` profile overlay mounts it as the `eu4-tools` row. The offline
overlay keeps it enabled (only the campaign goal plugin is disabled there).
`run.sh` exports `EU4_DSH_INFRA_ROOT` for the plugin's Python core; it must not
reuse `EU4_DSH_WORKSPACE`, which the campaign bridge reserves for its candidate
workspace assertion. `run-campaign.sh` mounts the plugin in its headless
overlay with `infra` pinned to the trusted checkout. Plugin tests:
`npm test --prefix dsh/eu4-tools-plugin` (Node `--test`, no EU4).
