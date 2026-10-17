# P3 synthetic OpenCode slice

**Status:** complete. PR-A (deterministic infrastructure) is implemented under
`oracle/slice/`; PR-B binds the pinned OpenCode binary
(`oracle/slice/opencode.py`, `oracle/slice/opencode_config/`), drives the pilot,
the pre-registration and the one declared acceptance run
(`oracle/slice/opencode_run.py`), and re-verifies publications through a
model-free guard (`tooling/common/check_oracle_receipt.py`, wired into CI). The
single declared evaluation, run `p3b-acceptance-20261017`, **certified** - visible
12/12, held-out 10/10 - with its sealed receipt published under
`docs/oracle/receipts/` and the narrative in
[P3B_ACCEPTANCE.md](P3B_ACCEPTANCE.md). Nothing here launches EU IV or implements
a recorder.

This document is authoritative for the P3 slice. The phase contract is in
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md); the preserved blueprint it refines is in
[BLUEPRINT_PROVENANCE.md](BLUEPRINT_PROVENANCE.md).

## Purpose

The P3 gate states that a **real OpenCode agent must repair a supplied defective
candidate using Oracle diagnostics, then pass a held-out verdict**, under
controller-enforced abort, budget and receipts. PR-A builds and proves the
deterministic half of that: the target, harness, comparator, agent-facing tools,
controller and receipt chain. PR-B binds the pinned OpenCode binary and performs
the real repair.

## Trust domains

| Domain | Owns | Never receives |
| --- | --- | --- |
| **Trusted Oracle host** (`oracle/slice/`) | visible **and** held-out corpus, build/run harness, comparator, controller, receipts | — |
| **Agent workspace** (fresh, per run) | the defective `candidate.c`, the public interface `synth.h`, `fixtures/visible/` | held-out fixtures, Oracle source, controller token |
| **Controller** | budget, attempts, wall clock, process tree, candidate freezing, the single held-out verdict, receipt sealing | — |

The agent-facing surface is exactly five MCP tools: `oracle_inspect`,
`oracle_fixtures`, `oracle_coverage`, `oracle_test`, `oracle_diff`. OpenCode
prefixes each raw MCP tool name with its server name, so the bridge declares the
bare names `inspect`/`fixtures`/`coverage`/`test`/`diff` under the `oracle`
server.

**Certification is controller-only.** `certify` is not in the MCP catalog; it is
an internal function reachable only through a privileged CLI guarded by the
controller's run token, and it returns an outcome-only verdict. The controller
invokes it **once**, after the candidate is frozen, and only for an *eligible*
run: a completed, non-aborted attempt whose visible suite passed. A failed
certification ends that evaluation; repairing again against the same held-out
suite would require a separately declared evaluation with fresh material, because
the old holdout would no longer be independent.

**Boundary, not sandbox.** Held-out isolation is an *application-level* boundary.
It is not a security guarantee against arbitrary malicious process access;
OS-level sandboxing is deferred to a later phase (see the preserved blueprint's
PR-F).

## The synthetic target

`oracle/slice/{target.c,reference.c}` implement one portable C11 function with
two observable domains:

```c
int synth_apply(struct synth_state *s, int mode, int arg);  /* struct: dirty, value, counter */
void synth_emit(int field, int value);                      /* ordered effect transcript */
```

`target.c` is defective in mode 0: it sets `dirty` but never writes `value`, so
both the raw post-state bytes and the effect transcript diverge. `reference.c` is
the trusted baseline.

Compilation flags are fixed and recorded: `-std=c11 -O0 -fwrapv
-fno-strict-aliasing`. `-fwrapv` makes the counter-overflow fixtures defined
behaviour rather than UB.

## Corpus

Fixtures are frozen ground truth under `oracle/slice/fixtures/`:

* `visible/` (12) — agent-accessible; exercises the defect and the neighbouring
  modes.
* `held_out/` (10) — trusted-host only; same contract, disjoint inputs, including
  integer extremes and wraparound.

`oracle/slice/gen_fixtures.py --check` is the **baseline qualification**: before
any agent is invoked, the reference must pass both sets byte-for-byte. Tests
report this, plus the fact that the defective target fails both.

## Harness and comparator

The harness — never the candidate — constructs the actual observation. Each
build copies the trusted driver and header plus the candidate into a private
directory, so a candidate that ships its own `synth.h` or `driver.c` cannot
change the ABI or the observation protocol.

* **Whole-group termination.** Every child starts in its own session, so its pid
  is also its process-group id, and that id is retained from creation. The
  *group* is signalled after **every** exit - timed out or not - and polled to
  extinction, escalating to `SIGKILL`; the group leader's exit is never treated
  as evidence that its descendants are gone. A leader that exits `0` while a
  background descendant is still alive therefore cannot leave it running, and
  the supervisor never needs to have "learned about" a group in order to kill
  it. On the common path the group is already empty and the cleanup is a no-op.
  Escalation is bounded in time, so a persistently-visible group (for example
  one containing a zombie) cannot loop. The guarantee is: descendants are
  terminated, and the controller's own children are reaped (orphaned
  grandchildren are not `waitpid`-able by it).
* **Bounded wall clock.** Compilation, the compiler-identity probe and every
  fixture run are bounded by the smaller of their operation-specific limit and
  the remaining run deadline, and an operation whose deadline has already passed
  does not start at all.
* **Bounded output.** Child stdout/stderr is redirected to files under an
  OS-enforced size limit (`RLIMIT_FSIZE`), so a process cannot consume unbounded
  memory or fill the disk; the writer is signalled when it exceeds the cap.
  Retained text is capped separately, and over-cap output is reported as
  *incomplete*, never as equality.
* **Strict transcript.** `RET`, `MEM` and `END` must each appear exactly once;
  the `END` count must be a non-negative integer that equals the number of `EFF`
  records; and no output may follow the terminator. Any violation is incomplete
  evidence.

`oracle/slice/compare.py` validates the actual observation envelope before
delegating to the prototype comparator in [core.py](../../oracle/core.py):

* missing domains, malformed hex or an incomplete transcript → `INCOMPLETE_ORACLE`;
* a candidate the compiler rejects → `CANDIDATE_BUILD_ERROR`;
* abnormal termination → `CANDIDATE_CRASH` or `CANDIDATE_TIMEOUT`, never `MISMATCH`;
* a trusted-harness fault → `HARNESS_ERROR`;
* only a complete actual observation against complete expected evidence can `PASS`.

## Budget, process tree and freezing

* **Budget** (`oracle/slice/budget.py`): a run-scoped SQLite ledger. Admission and
  reservation are atomic; the run id is immutable; an MCP restart cannot reset
  counters; missing or corrupted state denies rather than resets. Accepted calls
  consume budget even when the call then fails. Waiting for the write lock is
  bounded by the run deadline, so a contended ledger cannot outlive its run.
* **Process tree** (`oracle/slice/adapters.py`): children start in their own
  session; on timeout or abort the whole group is terminated and reaped, so a
  stalled or crashed agent cannot leave a compiler, test binary or MCP server
  running. A command whose deadline has already expired is not started.
* **Freezing** (`oracle/slice/workspace.py`): after the agent stops, its remaining
  processes are reaped and the candidate is copied into a read-only attempt
  snapshot. The controller compiles and certifies **only** from that snapshot, so
  a late writer cannot change the artifact that received the verdict.

## Run deadlines

Two clocks bound a run, and both are the controller's to declare:

| Deadline | Applies to |
| --- | --- |
| `run_deadline` | the agent, its MCP tool calls and the visible tests it initiates |
| `finalization_deadline` | candidate freezing, trusted visible verification, eligible held-out certification and receipt publication |

The finalization budget is separately declared and freshly started; it never
revives an expired run nor makes an aborted run eligible for certification.
(`RunConfig.finalization_seconds`; recorded in the receipt.)

The controller - never the adapter - owns the wall clock. The deadline is
re-asserted immediately after **every** attempt result, whatever its status, and
again after the candidate is hashed but before a `done` result may be accepted.
An adapter that returns `done`, `progress`, `gave_up` or `crashed` after the
deadline is therefore classified as a deadline violation; on the final permitted
attempt a late `progress` would otherwise be misreported as an attempt-limit
exhaustion.

The MCP server is a *separate process* and cannot ask the controller for its
remaining time. So the controller persists the run's identity and the absolute
monotonic deadline in trusted, controller-owned state
(`oracle/slice/runstate.py`, `run-state.json`), which the server loads at startup
and enforces before admission and throughout execution. The server **never**
accepts a deadline from tool arguments. Because a monotonic timestamp is only
meaningful within one boot, the state also records a boot token where the
platform provides one and the declared budget; if the clock appears to have been
reset, loading fails closed, and a server whose state is missing or provably
stale refuses to start rather than serving without a deadline. A non-finite
(`NaN`/`Infinity`) or overflowed deadline or budget is refused both when written
and when loaded: `time.monotonic() >= nan` is always false, so such a value would
make the server's expiry check meaningless.

## Receipts

The controller writes a terminal receipt on every path that **begins
finalization** - including after timeout, crash, a missing candidate, a candidate
the compiler rejects, or any trusted-host I/O fault during freezing, compilation,
corpus loading, certification or metadata collection. Setup failures that occur
*before* the run exists (creating the run directory, opening the budget ledger, or
materializing the workspace) are outside this guarantee: there is no run state to
report against yet. Every expected fault is converted into an explicit result
object *before* the receipt is constructed, so receipt construction cannot fail
for an expected fault; the finalization block's unconditional `finally`
guarantees the budget ledger is **closed** (cleanup, not receipt delivery); and a
receipt that cannot be *stored* is reported as an explicit `ReceiptError`. The
payload binds:

| Identity | Data |
| --- | --- |
| Source | baseline commit, defective and final candidate hashes, snapshot path |
| Corpus | visible and held-out manifest hashes, counts, schema version |
| Toolchain | compiler version, flags, harness/comparator/driver/interface hashes |
| Agent | adapter, model (filled in PR-B), tool calls, budget granted/limit |
| Execution | attempt sequence, timings, abort reason, finalization budget and duration |
| Verdict | visible-suite outcome, one controller-owned held-out verdict, run status, `candidate_verified`, `certification` |

**Abort-first classification.** A candidate may independently verify while its
agent execution failed; those are separate facts. The run status is `certified`
only when the agent completed, no abort condition was recorded, the snapshot
verified and both suites passed. An aborted, timed-out, budget-stopped,
no-progress or build-failing run is never `certified`, even if the frozen
candidate would have passed: the receipt preserves the visible verdict as
evidence (`candidate_verified` and `certification` record the distinction).

**Conditional certification.** Held-out certification runs **once**, and only for
an eligible run: a completed, non-aborted attempt whose visible suite passed.
Otherwise the receipt records `certification: "skipped: <reason>"`, so the
holdout is never spent on an ineligible attempt.

**Infrastructure failures outrank candidate verdicts.** A candidate may fail a
comparison, or the trusted host may be unable to *complete* one; the receipt must
not confuse the two. `execution.finalization.error` is set only for a
trusted-host fault - a missing or malformed corpus, an I/O failure while the host
freezes, compiles or certifies, a harness or toolchain that cannot operate, or a
compiler whose identity cannot be established - and its presence classifies the
run as `aborted`, never as a candidate failure. A fault the *agent* caused (it
deleted or replaced its own candidate) is recorded as `source.freeze_error`
alone, so the field still means "the trusted host could not operate".

| Actual outcome | Terminal classification |
| --- | --- |
| Candidate fails the visible comparison | `visible_failed` |
| Candidate fails a completed held-out comparison | `held_out_failed` |
| Corpus missing or malformed | `aborted` (`finalization.error`) |
| Trusted-host I/O fault during finalization | `aborted` (`finalization.error`, receipt still sealed) |
| Compiler or trusted harness cannot operate | `aborted` (`finalization.error`) |
| Candidate contains invalid C | `visible_failed` with `CANDIDATE_BUILD_ERROR` |
| Both suites pass **and** every controller gate passes | `certified` |

**Provenance gates acceptance.** An accepted result must identify its toolchain.
If the compiler's version cannot be established within the finalization budget,
the run is `aborted` with `certification: "failed: unknown compiler identity"`
rather than silently weakening the receipt's reproducibility claim. If a trusted
source cannot be *read* at all, its digest is recorded as an explicit `null` and
the run is `aborted` with `certification: "failed: incomplete toolchain
metadata"`; digests that could be collected are retained. In both cases the
verdict evidence itself (`candidate_verified`, the held-out verdict) is preserved;
only the acceptance claim is withheld.

A receipt is *sealed* when it is content-addressed and verifiable against a
trusted reference: the digest is computed over the canonical payload, not merely
stored next to it. Receipt verification is reproducible; the model trajectory is
not — rerunning an LLM at identical settings is not expected to reproduce the
same edits. Bulky logs and run directories stay local (`oracle/slice/runs/` is
gitignored); only the sealed receipt is published.

## PR-A acceptance criteria

| Criterion | Where it is proven |
| --- | --- |
| Reference passes visible and held-out; the defective target fails visible | `tests/test_oracle_slice_comparator.py` |
| Exactly five tools via MCP; `certify` undiscoverable and uncallable | `tests/test_oracle_slice_bridge.py` |
| Held-out never enters the workspace or any tool response | `tests/test_oracle_slice_controller.py`, `tests/test_oracle_slice_e2e.py` |
| Missing, incomplete or malformed observations cannot pass | `tests/test_oracle_slice_comparator.py` |
| Reservations hold across concurrent calls and MCP restarts; lock waits honour the deadline | `tests/test_oracle_slice_bridge.py` |
| Timeouts terminate the whole process group, including a `SIGTERM`-ignoring descendant, and still emit receipts | `tests/test_oracle_slice_controller.py`, `tests/test_oracle_slice_e2e.py` |
| A leader that exits successfully cannot leave a live descendant behind | `tests/test_oracle_slice_controller.py` |
| A late `done`, or a late `progress` on the final attempt, is a deadline violation | `tests/test_oracle_slice_e2e.py` |
| A missing or malformed corpus is an infrastructure failure, not a candidate verdict | `tests/test_oracle_slice_e2e.py` |
| An unidentifiable compiler prevents acceptance while preserving the verdict evidence | `tests/test_oracle_slice_e2e.py` |
| Unreadable toolchain metadata yields an explicit `null` digest, preserves the rest, and still seals a terminal receipt | `tests/test_oracle_slice_e2e.py` |
| A trusted-host I/O fault while freezing, verifying or certifying still yields a sealed terminal receipt | `tests/test_oracle_slice_e2e.py` |
| Non-finite or overflowed run state is rejected on write and on load | `tests/test_oracle_slice_bridge.py` |
| The compiler-identity probe is bounded by the caller's deadline | `tests/test_oracle_slice_comparator.py` |
| The run deadline bounds compilation and fixture evaluation; expired operations do not start | `tests/test_oracle_slice_comparator.py`, `tests/test_oracle_slice_controller.py` |
| Over-cap output is bounded in production and retention, and is never a normal run | `tests/test_oracle_slice_comparator.py` |
| The MCP server enforces the deadline independently and fails closed without run state | `tests/test_oracle_slice_bridge.py` |
| A candidate that does not compile still yields a terminal receipt with an error classification | `tests/test_oracle_slice_e2e.py` |
| An aborted run is never certified; certification is skipped when ineligible | `tests/test_oracle_slice_e2e.py` |
| The finalization budget bounds post-agent verification | `tests/test_oracle_slice_e2e.py` |
| Certification uses immutable snapshots | `tests/test_oracle_slice_controller.py` |
| Stub repair succeeds in budget; give-up, crash, timeout and no-progress terminate cleanly | `tests/test_oracle_slice_e2e.py` |

PR-A contains genuine MCP protocol smoke tests over a real stdio subprocess:
`initialize` → `tools/list` → `tools/call`, and a refusal-after-expiry test that
proves no compilation begins once the trusted deadline has passed.

## Non-goals and boundaries

* No EU IV launch, no recorder, no runtime capture. P4 is the first phase that
  implements generalized capture machinery; nothing here implies a recorder
  exists.
* The `StubAdapter` is a deterministic stand-in for the agent. It proves the
  infrastructure, not model capability; PR-B proves the latter.
* Token and cost limits are not claimed as absolute billing guarantees: provider
  usage may arrive only after a response. Only local call, attempt, wall-clock
  and process limits are enforceable here.

## PR-B: the pinned OpenCode binding

PR-B has two halves: the **binding** (this repository's code, testable offline)
and the **run** (one declared evaluation against the pinned binary and model).
The binding is implemented under `oracle/slice/opencode.py` with the tracked
configuration template in `oracle/slice/opencode_config/`; the run is driven by
`oracle/slice/opencode_run.py` and re-verified by
`tooling/common/check_oracle_receipt.py`.

### What was pinned against the installed binary

These were established by probing `opencode v2.0.30` directly, and they supersede
the preserved blueprint's V1 snippets:

* **Code mode is on by default** and wraps every MCP tool inside a JavaScript
  `execute` sandbox. The binding sets `codemode: false` both globally and on the
  server, which exposes the five Oracle tools as direct top-level tools
  (`oracle_inspect` … `oracle_diff`). This is required: a tool that is only
  reachable from inside `execute` cannot have its permissions observed at the
  tool boundary.
* **Permission precedence is last-match-wins**, and enforcement has two
  observable shapes. A whole-action `"deny"` removes the tool from the catalog
  (the model's catalog simply lacks it, and no event is emitted). A
  pattern-form restriction that leaves *some* invocation allowed keeps the tool
  present and rejects at call time with `part.state.status == "error"` and the
  structured refusal prefix `"Permission denied: …"`. The binding denies the
  dangerous actions outright (`shell`, `subagent`, `webfetch`, `websearch`,
  `browser`, `question`) and pattern-denies `external_directory`, so the pinned
  run produces **both** classes of evidence and the acceptance memo can
  distinguish a genuine denial from an absent tool.
* **Configuration discovery is explicit.** `OPENCODE_DIR` *et al.* do not
  redirect discovery, but `OPENCODE_CONFIG=<path>` loads a specific file. The
  binding therefore renders `opencode.json` **outside** the agent's workspace and
  passes it through `OPENCODE_CONFIG`, so the agent's own file tools cannot
  rewrite the permissions that constrain it. User configuration is isolated with
  per-run `XDG_CONFIG_HOME` / `XDG_DATA_HOME` / `XDG_CACHE_HOME` /
  `XDG_STATE_HOME`.
* **`PWD` decides the project directory.** The server resolves its project root
  from the `PWD` environment variable rather than from the child's real working
  directory, and `subprocess` does not update `PWD` when it sets `cwd`. An
  inherited `PWD` therefore made the **host repository** the agent's project
  root, which put `oracle/slice/fixtures/held_out/` *inside* the agent's own
  file-tool scope, where `external_directory` cannot deny it because it is an
  internal path. The adapter pins `PWD` to the workspace, which makes the project
  root correct under either reading. The pilot caught this; it is exactly the
  class of defect a stub can never find.

### Trust boundary

The agent's project root is `run_dir/workspace`, with `run_dir` under an absolute
staging root **outside the repository** (default
`~/.local/share/eu4-oracle-p3/<run_id>`). Three things must agree for that to
hold, and the pilot showed that one of them silently did not:

1. the child's working directory is the workspace;
2. `PWD` names the workspace, because OpenCode takes its project directory from
   `PWD` rather than from the real cwd - without this the *host repository*
   became the project root (see the `PWD` bullet above);
3. `OPENCODE_CONFIG` names a configuration **outside** the workspace, so the
   agent's file tools cannot rewrite the permissions that constrain it.

The workspace contains only `candidate.c`, `synth.h`, `fixtures/visible/` and
`README.md`. The MCP server imports `oracle.slice` from the host repository
through `PYTHONPATH`, so the repository remains symbolically reachable: this is
an **application-level** boundary, not an OS sandbox, and descendant containment
is likewise not a sandbox. Both are PR-F work.

### Pilot evidence

Before the single declared run, the pilot drives the binding against the pinned
binary with a **visible-only** fixture root and ends in a code-level `gave_up`,
so it cannot spend the holdout. Machine-observed, it established that:

* the pinned binary (`opencode v2.0.30`, digest recorded) and the pinned model
  (`opencode/deepseek-v4.1-flash-free#high`) resolve and run;
* the five Oracle tools are served as **direct top-level tools** - the server log
  records `mcp connected server=oracle tools=5`, and `tools/list` returns
  `inspect, fixtures, coverage, test, diff` with `function_id` and
  `candidate_path` both required;
* a **structured permission denial** is produced: a direct `read` of an external
  path returns `status == "error"` with `Permission denied: external_directory`;
* the other two shapes are distinguishable from a denial - a denied-outright
  action is *absent* (`No tool named "shell" is currently available`), and a
  malformed call is an argument error (`path: Missing key`);
* the held-out corpus is unreachable from the agent's project root:
  `glob **/held_out/*` returns nothing, and `glob **/*.json` returns only
  `workspace/fixtures/visible/*`;
* the full agent-facing path works end to end, model-free and repeatably:
  `tools/call test` on the workspace candidate compiles it and evaluates all 12
  visible fixtures (a repaired candidate passes; the defective one fails 4).

The pilot also fixed the `PWD` project-directory defect above and showed that the
pinned repair prompt must state the tool contract (including `candidate_path`)
explicitly.

### Acceptance criteria

| # | Criterion |
| --- | --- |
| C1 | The pinned binary, model and variant, plus the *effective* configuration, are bound in the sealed receipt |
| C2 | The pinned OpenCode process enforces its configured restrictions on its **direct** tools, with machine-observed denial evidence distinguishing denial from absent tool, model refusal and unrelated failure |
| C3 | Held-out fixtures are absent from the agent workspace and are never intentionally exposed through the agent-facing API |
| C3b | Exfiltration **detection**: no `hid-*` id or held-out expected-value fingerprint appears in any agent-visible surface |
| C4 | The tool-call count is ledger-authoritative, and the event stream proves a successful `oracle_inspect` and `oracle_test` |
| C5 | A real repair: the final candidate differs from the defective one, passes visible **and** held-out, and the certification ran |
| C6 | The sealed receipt and committed artifact are re-verified by a model-free CI replay, cross-checked against a pre-registration committed **before** the run |
| C7 | The run is bounded and not aborted |
| C8 | The pre-registration was pushed before the run |
| C9 | Honest scope: 22 fixtures are not universal equivalence, certification is not proof of repair, the holdout is spent once, and group termination is containment rather than a sandbox |

### Evidence and accounting invariants

* **Ledger-authoritative accounting.** A real agent calls the Oracle through the
  MCP **server process**, so the controller's in-process counter would read zero.
  Per-attempt and total counts come from the shared SQLite ledger;
  `receipts.granted()` is the exact count of agent-initiated calls in either case.
* **Complete bounded evidence.** Retention caches only 256 KiB of a child's
  output, but the event stream is a full capture file (OS-capped at 8 MiB). The
  binding is built from the file and **fails closed** on a missing, empty,
  malformed or production-capped stream, and records the digest and byte length
  before teardown.
* **Binding is validated by the controller.** The adapter reports identities; the
  controller checks their shape and folds them into `toolchain.opencode` before
  sealing. A partial or malformed report is a finalization error and the run is
  not acceptable.
* **Replay integrity.** The guard refuses to replay a published receipt unless
  the current harness, comparator, driver, interface and bridge still hash to the
  pinned identities, because replaying against a changed oracle proves nothing.
* **Pre-registration.** `opencode_run.py --mode preregister` emits the pin on the
  same code path the run binds, so the declared digests cannot drift from what the
  run records. It fixes the run id and staging root, because the materialized
  configuration digest depends on the absolute run directory. The declaration is
  committed and pushed *before* the run; the guard reports a declaration whose
  receipt is not published yet as **pending** rather than failed
  (`--require-receipts` tightens that), so CI stays meaningful across the window
  between the two commits.

### Tests

| Property | Where |
| --- | --- |
| The tracked template renders valid JSON with the pinned restrictions | `tests/test_oracle_slice_opencode.py` |
| The child's `PWD` is pinned to the workspace and its isolation roots lie outside it | `tests/test_oracle_slice_opencode.py` |
| The materialized config's server block really lists the five tools and tests the candidate | `tests/test_oracle_slice_opencode.py` |
| A malformed, empty or non-object event stream is refused | `tests/test_oracle_slice_opencode.py` |
| A permission denial is normalized, and a refusal or absent tool is not one | `tests/test_oracle_slice_opencode.py` |
| A real repair through the adapter is certified and bound end to end | `tests/test_oracle_slice_opencode.py` |
| Held-out material in the stream, or a production-capped stream, prevents acceptance | `tests/test_oracle_slice_opencode.py` |
| Probe mode is non-accepting and spends no holdout | `tests/test_oracle_slice_opencode.py` |
| A clean publication verifies; tampered, reordered or replay-incompatible ones do not | `tests/test_check_oracle_receipt.py` |

The acceptance run itself is published as a sealed receipt under
`docs/oracle/receipts/`, the committed artifact under `docs/oracle/artifacts/`,
the sanitized event summary under `docs/oracle/agents/`, and the pre-registration
under `docs/oracle/preregistration/`, with the narrative in
[P3B_ACCEPTANCE.md](P3B_ACCEPTANCE.md).
