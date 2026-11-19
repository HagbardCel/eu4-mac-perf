# M7 final readiness report

Date: 2026-11-19 (Europe/Berlin)
Verdict: **READY — M7 operational infrastructure and autonomous-campaign workflow.**

Scope: the implementation is merged, a genuine multi-round DSH campaign completed
with independent checker acceptance, and one bounded authentic pilot captured,
sealed and diagnosed with the repaired in-process Metal shadow path. M7 is **not**
a claim of OpenGL/Metal pixel equivalence: the authentic event-17 difference
remains open and the next evidence request is recorded below.

## Merged implementation

| PR | Merge commit | Scope |
| --- | --- | --- |
| #214 Multi-checkpoint capture | `0068127` | Bounded `fb:0` checkpoints per frame, budgets, causal localization |
| #215 Independent correction acceptance | `a3bca8a` | Artifact-recomputed acceptance, mandatory regression inventory, readback/revision binding |
| #216 Campaign resource guard | `df16667` | Atomic reservations, one-use launch claims, fail-closed recovery, correction accounting |
| #217 Native blob verifier | `8943bcd` | Size-unknown repeated blob references in the sealed event stream |
| #218 This report | this head | Final evidence and closure |

`main` after the merges: `8943bcdd`; after the #218 documentation merge:
`cecc727c`.

## Post-completion hardening (2026-11-19)

Two residual correctness issues were closed on a focused branch without
reopening the renderer or repeating the pilot:

- **One-use launch reservations.** An open reservation is a read-only preflight;
  each physical spawn site (`run`, `run_ops`, `gfx_launch.spawn`) atomically
  claims the reservation's `eu4_launches` entitlement immediately before
  `subprocess.Popen`. A second or concurrent claim is refused, stopped/expired
  campaigns are rechecked, and a failed spawn keeps the consumed allowance.
- **Fail-closed checker accounting.** `validations.jsonl` now carries a durable
  attempt lifecycle (start before the checker, terminal result after it, distinct
  attempt IDs). Interrupted and exceptional attempts count against
  `max_validations`; finalization's fresh run and frozen-candidate replay are
  charged too; the administrative `checker-compat` probe is not.

This hardening is verified offline. It does not alter the pilot evidence, the
controlled two-round correction, or the `[14, 17]` localization.

Portable CI passed on every merged head (`ea554cae`, `01b3b60b`, `3800f883`,
`c1ea34ab` and their branch-update heads). Focused macOS suites passed; the
native repeated-blob host accepts the preserved authentic capture
(`blob_references=0`). The final integrated offline gate passed before the merge
commits (721 s, `20261119T182911Z-a2cfafcc`, 0 failures); the merge commits
introduce no code changes.

## Autonomous campaign

### Controlled two-round correction (recorded)

- Worktree `wt_m7_campaign`; controlled renderer-relevant fault `71dbd4bb`
  (captured `RGBA8` textures translated as `BGRA8`).
- Two genuine native DSH goal rounds: round 1 localized the earliest divergence
  (event 20, `fb:0`, program 301, producer 19, interval [14, 20]); round 2
  applied the minimal fix `eb6a9b24`, rebuilt, replayed and submitted to
  `correction_accept`.
- Independent checker: **accepted**; 1,925,408/255 → 1,161,075/75, 0 problems,
  no equivalence claim. Budget: 1 replay, 1 correction, 0 EU4 launches.
- Correction attempts are now reserved atomically at the acceptance boundary;
  rejected/invalid verdicts are failed attempts with their verdict and problem
  codes audited.

### Documented `eu4-web` launcher

`./dsh/scripts/run.sh --campaign dsh/campaigns/m7-launcher-smoke.json
--campaign-state .local/m7/launcher/state.json` prepared and anchored the
isolated seed workspace, exported a fresh zero-launch external state, printed
the objective and exited through the Web app's `--help` without starting a
session. Creating the goal in a fresh `eu4-web` Web/TUI session requires the
operator; the running `web` session was used for the recorded two-round
campaign. The `eu4_*` tool surface of `eu4-web` was therefore not exercised in
this environment; this is an explicit operator dependency, not a substitute
with one-turn calls.

## Authentic bounded pilot

One EU4 launch under the external budget; no retry.

| Field | Value |
| --- | --- |
| Session / pid | `2564021f-4e00-4a58-a448-043697249a91` / 54668 |
| capture_frame_id / present / scene | 1 / 615 / 615 |
| Operations | 325 (315 draws, 9 clears, 1 copy) |
| Dimensions | 1728 × 1117, BGRA8, lower-left |
| Manifest SHA-256 | `89776f8c13d5bbb7b7ac962e04fac1e8ce6cd93d792f0b82dc35aa11e0931552` |
| Event-stream SHA-256 | `33b9044a1ea33ab425a2f7173e4ae898d5d6395d1c35b3576fa49f99052c19ad` |
| Reference SHA-256 | `9283ae21e3d8fd43559d2978b7f33d290a124aef2f32dcd5163c12dd607bea92` |
| Checkpoints requested/returned | 11 / 11 (`fb:0`: 14, 17, 19, 20, 284) |
| In-process shadow | `execution=ok`, `identity=verified`, `provenance=verified`, `operation_coverage=complete`, `state_coverage=partial` |

Same-frame `fb:0` comparisons in this new frame:

| Event | Status | Mismatched pixels | Max channel error |
| --- | --- | ---: | ---: |
| 14 | match | 0 | 0 |
| 17 | divergent | 391,120 | 79 |
| 19 | divergent | 666,261 | 79 |
| 20 | divergent | 875,960 | 79 |
| 284 | divergent | 1,235,098 | 99 |

Final frame: 1,160,428 / 1,930,176 differing (60.12 %), max channel error 67.

- First confirmed divergence: **event 17**, `fb:0`, `_GfxDrawIndexed`, program
  301, caller `0x10d4e50`, producer **event 16**.
- Same-frame causal interval: **[14, 17]**, lower bound `same_resource_match`
  (event 14 matched exactly in this capture).
- Next diagnostic checkpoint proposed by the existing machinery: **event 15**
  (fresh frame; checkpoints from this capture must not be combined with any
  other frame).

Diagnostic conclusion: **requires_additional_evidence**. The interval narrows
to operations 14–16, but the evidence does not identify a specific renderer
state or shader cause; `rendering_state_coverage` is partial. No renderer
correction was attempted (0 of 1 correction evaluations used; 0 of 2 replays
used). The precise request is a fresh sealed frame with `fb:0` pre-event
checkpoints at **15 and 16** (retaining 14/17 bounds), with its own identity and
its own interval.

## Cleanup and budgets

- EU4 `terminated=true`, `killed=false`; no orphan game or coordinator process.
- Background display `EU4 Test` (main=false); physical display unchanged.
- GOG `settings.txt` restored byte-for-byte to its pre-capture SHA-256
  `62b4902a3a999ad3…dd496`.
- External state: 1 launch, 1 sealed frame, 1 shadow execution, 0 replays,
  0 corrections; no stop, no outstanding reservation, no automatic merge.

## Readiness criteria

| Criterion | State |
| --- | --- |
| Multi-checkpoint capture and same-frame interval on authentic evidence | **Met** (new frame [14, 17]; historical frame [14, 20] kept separate) |
| In-process Metal execution and provenance | **Met** (verified identity/provenance, complete operations; state coverage partial) |
| Independent correction acceptance | **Met** (controlled seeded correction accepted; authentic correction not attempted) |
| Genuine multi-round DSH campaign | **Met** (2 native goal rounds, checker-controlled acceptance) |
| Documented `eu4-web` launcher | Preparation/state/objective **met**; session/goal creation is an operator step |
| Resource limits and stop conditions | **Met** (external reservations, deadlines, cleanup) |
| Portable CI / full offline gate / native verifier | **Met** |
| Pixel-perfect OpenGL/Metal equivalence | **Not claimed** (event-17 difference open) |

## Remaining limitations and next work

- The authentic renderer difference is localized to `[14, 17]`, producer event
  16, but not root-caused; the next capture should add checkpoints 15 and 16.
- `rendering_state_coverage` is partial; no equivalence claim.
- A fresh `eu4-web` goal session must be created by the operator to exercise the
  launcher's `eu4_*` tool surface end to end; the campaign logic itself was
  demonstrated in the running Web session.
- The controlled correction is evidence for the autonomous loop, not a repair of
  the authentic defect.
- The one-use claim and checker-attempt lifecycle are qualified by focused and
  offline tests; no additional live EU4 launch was performed to re-demonstrate
  them, and the recorded pilot remains the authoritative live evidence.
