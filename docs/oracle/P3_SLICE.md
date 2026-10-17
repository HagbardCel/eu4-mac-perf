# P3 synthetic OpenCode slice

**Status:** PR-A (deterministic infrastructure) implemented under `oracle/slice/`.
PR-B (the pinned real OpenCode run) is not yet performed. Nothing here launches
EU IV, uses a model, or touches the network.

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

## PR-B (next)

Bound the pinned OpenCode binary (`opencode v2.0.30`), configure the `oracle`
local stdio MCP server with `codemode: false` and a deny-by-default permission
set (`shell`, `subagent`, `webfetch`, `websearch`, `external_directory` denied;
candidate editing and the five Oracle tools allowed), prove a real tool
invocation in an isolated service/session context, run the real repair with
`opencode/deepseek-v4.1-flash-free#high` in an isolated candidate directory, and
publish the sealed receipt and acceptance memo.
