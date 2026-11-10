# DSH native-goal integration -- status, evidence and limitations

Architecture and operator guide: [docs/dsh-native-goal.md](../docs/dsh-native-goal.md).
This file records where the work stands, what has actually been measured, and
what is deliberately not done. The development diary lives in the git history.

## Where it stands

A second, independent migration path: DSH owns the model loop, tool execution,
session lifecycle and goal continuation, and the EU4 project supplies the campaign
contract, the trusted checker and the qualification record. OpenCode is untouched.

| Phase | State |
|---|---|
| 0 -- genuine autonomous run | **passed** (headless, single turn) |
| 1 -- native services, profile, provisioning | done |
| 2 -- shared acceptance and campaign contract | done |
| 3 -- bridge as an interface | done (6 operations) |
| 4 -- thin plugin | done (2 tools + 1 conditional) |
| 5 -- one snapshot implementation | done |
| 6 -- removal and documentation | done |
| 7 -- full matrix and live multi-round test | matrix green; multi-round **proven**; Web completion and live review outstanding |

## What is shared, not duplicated

| Concern | Home |
|---|---|
| Running a checker and reading its verdict | `automation/acceptance.py` (both backends import it) |
| Campaign fields: objective, checker, manifest, workspace, timeout | `automation/campaign_contract.py` |
| Candidate delta and snapshot | `automation/candidate_snapshot.py` |
| Manifest and evidence digests | `automation/verification_manifest.py` |
| Review report schema, reviewer receipt | `automation/review_result.py`, `automation/handoff.py` |
| Goal lifecycle, rounds, sessions, sandbox | DSH |

`automation/runner.py` calls the shared acceptance; `automation/state.load_campaign`
and `automation/dsh_campaign.load_sidecar` both consume the shared contract.
Byte-for-byte preservation was not a goal -- semantic preservation was, and the
OpenCode suite is the evidence: 86 tests, unchanged in behaviour throughout.

## Measured evidence

**Native multi-round continuation, live Web session** (the Phase C result):
session `session-317a33be-9418-4347-bb4f-d850b5755137`, goal
`goal-f46ca1cf-3b06-4d67-b4ff-c604b5c41fd8`, created by a human-typed `/goal` and
then driven entirely by the goal-round driver: **rounds 1, 2 and 3 were injected
in the same session with no prompt from the operator between them**
(`roundsStarted: 3`). The agent ran tool calls and called `eu4_validate_candidate`
twice. The goal ended `blocked`, and the reason is a real defect this run exposed:
the Web session had been created in `~/Documents/deepseek-harness/default-workspace`,
so the campaign workspace inside the checkout was outside the sandbox and the
plugin refused it (`workspace_mismatch`). A DSH Web session takes its working
directory from the workspace registry, not from the process cwd.

`run.sh --campaign` now registers the checkout in that registry and makes it the
default, which is what the UI's "open folder" does, so the operator does not have
to pick a workspace. Verified idempotent and that the default resolves to the
checkout. **Not yet re-run live**: a Web campaign that reaches a checker pass and
finalization. The headless path has completed one end to end (below).

**Autonomous smoke campaign** (`dsh/scripts/run-campaign.sh`, headless):
session `session-f7427783-9fe5-499a-80a8-de11071ae8f9`, cwd equal to the campaign
workspace, goal `goal-05f2a045-9df4-4ff6-8847-9b9d1fc706be` created by the model
and driven to `complete`, tool order `create_goal` -> read/write/edit ->
`validate` (fail) -> `validate` (pass) -> `finalize` (pass), receipts
`b0685081-r1`/`-r2`, finalization frozen at seed digest `b0685081`, candidate
`marker.txt` exactly `ready`. The completion guard never let the model
self-certify. This run also found the `agent.sessionId` defect: the id lives on
`agent.session.id`, and the missing value was stringified into the record.

**A0 trusted validation** (bridge, real checker, installed EU4 bundle):
`checker-compat` ok; `validate` failed on `candidate.table != frozen
instruction_static table`; repair taken from `primitive-table.evidence.json`;
`validate` passed (receipt `d3d29099-r2`, manifest `da30a64f...`); `finalize`
re-ran the checker and froze the digest. The written evidence for this predates
the qualification-record consolidation; the run root still holds it.

**Live independent review**: session `session-ab907543-6ca8-4901-bac0-3bbc1dd0e9aa`,
outcome `unresolved`, one P1 `evidence_gap` finding, `qualification_mode: live`,
strict verification refused, `finalize` refused `review_required`. Its finding was
substantively right: the oracle digest it was given was sha256 of an *empty*
evidence list, so review now refuses unless the campaign declares a manifest or
evidence paths.

**Campaign screening** (`checker-compat`), which is why the pilot is the one it is:

| campaign | baseline | result |
|---|---|---|
| `eu4-re2-pilot` | pass | unsupported -- a completed campaign cannot distinguish a candidate |
| `eu4-index-buffer` | fail | unusable: the pin edits a trusted file |
| `eu4-set-scissor-rectangle` | unavailable | unsupported: checker does not match the pin |
| `eu4-vertex-buffer-multiple` | fail | selected |

**Ghidra**: two concurrent `analyzeHeadless -readOnly` queries cannot both run --
the second aborts with `ghidra.framework.store.LockException`. A read-only replica
(`dsh/scripts/ghidra-replica.sh`, 701 MB) removes the contention: a shared-project
query and a replica query run concurrently, both exit 0, identical evidence.

**Original-EU4 capture**: never launched from this path. `automation.launch_ledger`
is accounting, not mutual exclusion (two callers under a limit of three both get
reservations); the DSH bridge imports no launch path at all.

**Runtime**: requalified on DSH `0.2.2-rc.1`, which the environment upgraded to.
Downgrading a global install the running Web GUI depends on is not this PR's call,
so `runtime.json` was repinned after verifying what the integration actually uses:
the goal service, `ctx.tools.guard` with the same denial-only `ToolGuard` contract,
the headless `--json` event types, `skill-filesystem` configuration and the session
header `cwd`. Profile checks pass: 2 tools and 1 guard, or 3 tools when the
campaign requires review; one skill in the catalog.

**Tests and CI**: 82 DSH tests, 24 node tests, 86 OpenCode tests, full repository
discovery green, CI green on Python 3.10 and 3.14.

## Known limitations

- **`dsh --profile headless` answers one turn.** Measured: a goal created in that
  turn gets no further round. `run-campaign.sh` is therefore for campaigns that
  finish in one turn; multi-round campaigns use the Web path.
- **Multi-round continuation works** (3 rounds in one live session, no prompt
  between them), but the run that proved it ended `blocked` on a workspace
  binding defect rather than on the checker. The binding is fixed and the fix is
  unverified live: no Web campaign has yet reached a checker pass and
  finalization, and no Web goal has reached `complete`.
- **No live reviewer rejection/remediation loop.** The channel, the schema
  validation and the receipt writing are exercised with a scripted reviewer; the
  live reviewer was run once and returned `unresolved`.
- **Original-EU4 live capture is disabled** and will stay disabled until every
  launch path shares a host-wide process lease. Worktrees pinned before the ledger
  existed cannot participate at all.
- **The pilot's qualification record sits in an agent-writable directory**
  (`<checkout>/.local/dsh/...`). Authority is unaffected -- completion requires a
  fresh checker run -- but production campaigns should keep records in the trusted
  state root, which needs a run-root/receipt-root split.
- **`danger-full-access` refuses qualification** by design; workspace-write is
  asserted from the harness's session header, not from an environment variable.

## Deferred deliberately

A shared process lease for original-EU4 launches; a replacement Ghidra query
engine; a multi-round headless facility; a general campaign platform; any
per-campaign or per-function special-casing in the plugin.
