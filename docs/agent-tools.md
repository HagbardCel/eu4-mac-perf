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
    "qualified": false
  },
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

### `eu4_replay`

```sh
python3 -m automation.agent_tools eu4_replay case --case <case-dir>
python3 -m automation.agent_tools eu4_replay frame --frame <trace-dir> \
  --shaders <shader-dir> --out <dir> --only-event 92
```

The case comparison is fully offline. A missing NumPy is `unavailable`, never a
successful diagnostic; a successful comparison with a difference verdict is
`ok` with `verdict: "different"`. The frame replay path is the existing
selected-event replay host; it does not require a new EU4 launch.

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
that process. Processing is bounded (`max_pairs`) and difference PNGs are
written only with `--temporal-artifacts`.

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
CLI exit 2) — never as a completed pass. A base/candidate comparison requires
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

`dsh/eu4-tools-plugin` (`@eu4/dsh-tools`) registers the seven names as native
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

## Metrics

```sh
python3 -m automation.gfx_metrics report \
  --root .local/infra/dual-1 \
  --ledger .local/infra/dual-1/authority/dual-6.json
```

Counts launches from the authoritative ledger, observed/paired/aligned/compared
frames, inconclusive comparisons, artifact bytes and offline cases. Inconclusive
work is never folded into a pass.
