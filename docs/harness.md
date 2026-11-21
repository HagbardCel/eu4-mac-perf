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
  --module tests.test_m7_diagnosis --module tests.test_agent_tools
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
| Interrupted reservation | Inspect the guard/lease/owned PID before retrying. An open reservation is the durable record; another launch is not automatic. |
| Identical deterministic failure | Change the candidate, diagnostics or evidence. A second unchanged failure pauses as `no_progress`; do not spend another round on the same input. |
| Terminal shadow result | The owned launch is stopped and settings restored; optional ops traces may be absent and are reported as such. |
| Stale/foreign receipt | Fail closed. Re-run the producer for the current candidate/seal; never repair a receipt by editing it. |

## Budget and leases

EU4 launches are serialized by the host-wide process lease plus a one-use
campaign claim. A campaign refusal happens before build/spawn. Replays and
shadow executions are charged through the same guard; nested entry points share
one reservation. GPU service time is not inferred from wall clock.
