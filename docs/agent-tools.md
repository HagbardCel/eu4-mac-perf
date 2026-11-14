# Structured EU4 reconstruction tools

One Python core serves every agent surface. The CLI prints exactly one JSON
envelope; the DSH/OpenCode adapters are thin argument mappers.

```json
{
  "schema": "eu4-agent-tool/v1",
  "tool": "eu4_diff_inspect",
  "ok": true,
  "status": "ok",
  "reason": "",
  "result": {}
}
```

Exit codes: `0` ok/pass/completed, `2` denied/inconclusive/not-found, `3`
usage or unexpected error. A refused launch is a structured result, not a
traceback.

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
python3 -m automation.agent_tools eu4_session inspect --run <session-dir>
python3 -m automation.agent_tools eu4_session terminate --run <session-dir>
```

`terminate` signals only the process identities recorded in that session, after
re-verifying pid/start-time/executable. It never enumerates `eu4` processes.

### `eu4_capture`

```sh
python3 -m automation.agent_tools eu4_capture health --run <session-dir>
python3 -m automation.agent_tools eu4_capture frames --run <session-dir> --limit 40
python3 -m automation.agent_tools eu4_capture request --run <session-dir> --role candidate --frame 42
python3 -m automation.agent_tools eu4_capture probe --checkpoint after-present \
  --output <dir> --campaign-run-root <authorized-root>
```

`probe` is a pass-through to the existing live launch path and still requires an
operator ledger. `health` distinguishes execution failure from instrumentation
failure and never treats command completion as pixel correctness.

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
failure, preserves evidence, and terminates only its own processes.

### `eu4_diff_inspect`

```sh
python3 -m automation.agent_tools eu4_diff_inspect --run <session-dir> --build-case
```

Returns stop/health/alignment/first-pair evidence and, with `--build-case`, the
package path. The package's `analysis.md` separates confirmed observations,
excluded explanations, hypotheses, missing evidence and the next inspection
point.

### `eu4_replay`

```sh
python3 -m automation.agent_tools eu4_replay case --case <case-dir>
python3 -m automation.agent_tools eu4_replay frame --frame <trace-dir> \
  --shaders <shader-dir> --out <dir> --only-event 92
```

The case comparison is fully offline. The frame replay path is the existing
selected-event replay host; it does not require a new EU4 launch.

### `eu4_case_inspect`

```sh
python3 -m automation.agent_tools eu4_case_inspect --case <case-dir>
```

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
Plugin tests: `npm test --prefix dsh/eu4-tools-plugin` (Node `--test`, no EU4).

## Metrics

```sh
python3 -m automation.gfx_metrics report \
  --root .local/infra/dual-1 \
  --ledger .local/infra/dual-1/authority/dual-6.json
```

Counts launches from the authoritative ledger, observed/paired/aligned/compared
frames, inconclusive comparisons, artifact bytes and offline cases. Inconclusive
work is never folded into a pass.
