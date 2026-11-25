# R0 repository audit

**Status:** audit delivered for disposition and PR-sequence review. [Approved plan baseline](https://github.com/HagbardCel/eu4-mac-perf/blob/c6ce0082e1755f94a01b6dfe04c8fe22679adc63/plans/repository-refactor/approved-plan.md) · [Dispositions](r0-disposition.md) · [Evidence and migration](r0-evidence.md) · [Measurements](r0-measurements.md)

R0 covers the repository and its verification boundaries. The leading refactor opportunities are to preserve findings and evidence, extract reusable checker behavior from milestone entry points, and measure repeated work before removing tests. A single retained capture establishes an exact RGB match through event 0 and a difference at event 1. Ordinary DSH workspace mode supplies native round continuation. Its filesystem policy and session persistence cover only part of the proposed trust and recovery model.

## Pinned inputs

| Input | Identity or result |
| --- | --- |
| Repository | `main` and local `origin/main` at `6710a341d864466d7b0ab2d297ab513922d582b6`, tree `eeb0c633cefa955900d334882efc8b157681403c` |
| Planning source | [Preserved plan](https://github.com/HagbardCel/eu4-mac-perf/blob/c6ce0082e1755f94a01b6dfe04c8fe22679adc63/plans/repository-refactor/approved-plan.md), SHA-256 `4b88dc2be8d4f93145d127959267a0a776824c49cb95b00d5cd0b65893f46e86`. The submitted source SHA-256 is `3bede981d415ad92af4239fa86307049de560c485bf3f8fc0f5661b04e317c11`. The tracked copy normalizes Markdown hard breaks and placeholder links in an example. |
| Overlap | GitHub returned zero open PRs at intake, before R0 PR #300. Main already includes #296–#299 numerical recipe identity, event-1 localization, sparse observation, and capture diagnostics. Preserve those paths and the scope of [#278](https://github.com/HagbardCel/eu4-mac-perf/issues/278). |
| Workspace | 71 registered Git worktrees at intake, including historical DSH campaigns and detached verification trees. The primary workspace contained local evidence and an untracked plan. A separate clean checkout pinned each full-suite attempt. |
| Host | macOS 27.0.1 build 26A434, arm64, Xcode 27.2 build 27B52, Python 3.14.10, NumPy 2.5.5. `clang`, `metal`, `glslangValidator`, `spirv-cross`, and Rosetta support files were present. |
| EU4 | Installed GOG macOS x86-64 binary SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` matches the pin. R0 read the file and code window offline. |
| DSH | `0.2.2-rc.5`, Node `v26.13.1`, pnpm `12.23.0`. Installed lifecycle package sources and pinned package identities were available. |
| Local audit outputs | `.local/r0/6710a341d864466d7b0ab2d297ab513922d582b6/`. This directory holds the counting recipe, generated inventory, verification records, and private timing samples. |

The dated status snapshot and the older R0-M profiling experiment have their own revisions and scopes. [Status](https://github.com/HagbardCel/eu4-mac-perf/blob/6710a341d864466d7b0ab2d297ab513922d582b6/docs/status.md) · [Target pin](../../reverse_engineering/target.json) · [R0-M profiling experiment](../../docs/r0-measurement.md)

## Source baseline

| Responsibility | Files | Logical bytes | Authored physical lines |
| --- | ---: | ---: | ---: |
| Renderer | 56 | 1,234,232 | 29,249 |
| Reverse engineering | 60 | 790,414 | 20,137 |
| Tooling and experiments | 325 | 5,669,142 | 134,441 |
| Orchestration and CI | 127 | 1,434,562 | 36,287 |
| Tests | 424 | 5,569,559 | 127,292 |
| Documentation | 480 | 3,037,631 | 47,627 |
| Generated, data, and evidence | 916 | 82,379,560 | — |
| **Total** | **2,388** | **100,115,100** | **395,033** |

- Use `RepoSnapshot.from_ref` and `repo_audit.generate` with the pinned commit and the existing lifecycle policy. Count each tracked path once using its Git blob size. Equal blobs at distinct paths count toward working-tree bytes. For authored UTF-8 text, count physical LF-delimited lines and a final unterminated line, including blank lines and comments. Run the same recipe at R7 and assign ownership consistently to both revisions so a move contributes zero net LOC. The local recipe is `measure-inventory.py` (SHA-256 `7292c532eb0c42be0d85db2de89a5dcb25317f2addf687639e55154ee9227c14`). Its classified output `authored-inventory.csv` has SHA-256 `9e2ee689fa04ed1e758744f12f6e6be05cdb2f32adddc1635f29506c18b1b93a`. [Snapshot reader](../../tooling/common/repo_snapshot.py) · [Audit generator](../../tooling/common/repo_audit.py)
- Assign authored code by primary responsibility. The renderer bucket contains the current Metal implementation, native replay code and handwritten constant-buffer semantics. RE includes extraction and original-execution tracing. Orchestration includes campaign control and manifests, DSH integration, and CI. Tests include repository and DSH tests and their native test hosts. Tooling includes the function-oracle infrastructure, capture, validation, reusable profiling, and analysis tools. JSON data and fixtures, binary assets, and seven known generated inventory or source files contribute bytes. Their line counts are excluded. The pass identified no separately vendored paths. Checked-in rendered images and historical result data retain their evidence disposition.
- The old lifecycle policy yields 955 active, 95 shared, 413 frozen, 51 retired, 28 generated, and 846 unknown paths. It flags 70 archive candidates, nine dependency conflicts, and zero eligible archives. These are the reproducible policy outputs. R0's reviewed dispositions use actual consumers and evidence dependencies. [Historical policy](../../tooling/common/repo_lifecycle.json) · [Disposition map](r0-disposition.md)

### Dependencies and complexity

The static pass found 1,910 imports, 174 includes, 19 CI references, 1,352 doc links, and 14 manifest references. Import, include and CI counts below deduplicate the caller/callee pair. Dynamic call sites, case descriptors, and operator entry points require source review.

| Priority | Source | Why changes need a narrow boundary |
| --- | --- | --- |
| 1 | Capture and native replay | The frame trace is 9,688 lines and the Metal frame host 7,116. The trace drives resource and attachment identity, while the replay host shares resource, state and shader code with the engine-facing paths. Follow the compiled dependency list and the event, failure, and lifetime tests before dividing files. [Frame trace](../../benchmark/eu4_gfx_frame_trace.c) · [Metal replay](../../benchmark/eu4_gfx_frame_replay.mm) |
| 2 | Replay and qualification | `gfx_independent_replay` has 30 static callers and `m6_frame` has 29. Their responsibilities include parsing, native build/translation, input materialization, output reconciliation, packaging, and CLI presentation. Preserve the producer and scope checks when moving orchestration. [Independent replay](../../automation/gfx_independent_replay.py) · [Frame pipeline](../../automation/m6_frame.py) · [Prefix checker](../../automation/gfx_prefix_check.py) |
| 3 | Python and native DSH entry points | `agent_tools` reaches 25 static dependencies. The JS bridge marshals argv, chooses a trusted Python core, and spawns separately from the candidate cwd. Audit tool registration, subprocess cwd and environment, and operator-doc examples as one interface. [Python tool registry](../../automation/agent_tools.py) · [DSH adapter](../../dsh/eu4-tools-plugin/lib/bridge.js) |
| 4 | Shared launch and analysis | `eu4_benchmark` has 46 static callers and `launch_lease` has 27. `eu4_metal_boot` looks like a milestone module but remains an active import from launch, live-Gfx, display, and reconstruction acceptance. The underlying frame-model audit and Phase C evidence verifier still run in CI. [Launch boundary](../../automation/gfx_launch.py) · [Frame-model command](../../benchmark/eu4_frame_model.py) · [Portable CI](https://github.com/HagbardCel/eu4-mac-perf/blob/6710a341d864466d7b0ab2d297ab513922d582b6/.github/workflows/profiler.yml) |

Native call paths include `eu4_gfx_observe_entries.def` and gateway stubs, the `_Gfx*` registration and patch table, the native build dependency snapshot, and the function-oracle recorder/boundary. Keep canonical symbol and target pins with the RE owner. Feed CLI and DSH through the same Python entry point during migration. [Observation registry](../../benchmark/eu4_gfx_observe_registry.py) · [Entry patcher](../../benchmark/eu4_gfx_entry_patch.c) · [Build dependency closure](../../automation/replay_host_dependencies.py)

## Graphics and evidence

### Reproduced scope

Seal `47590918-d63a-4798-9737-df00285ad51b`, frame 1, present 72, has 283 operations. The original frame is 1728 × 1117. The seal manifest is `e4696cd34eca08a3b6ce364e2c7a8bf2135efe11d8c5dab8bf1cc4a83abe51da`. The trusted root is `b3e3d7b5d1e4cefe814f460c99a19c32011242d100a8b0c5bdd97aa9e485756c`.

1. Bounded `eu4_session discover` against the primary, capture, and replay worktrees' evidence roots returned zero matching records and no case locator. The checked-in, explicit trusted-root reference resolved and verified all 1,192 files. R3 needs a verified publication and applicability path for this class of reference.
2. Restored the complete trusted root and its unchanged relative-path descriptor into a fresh private directory. The member inventory and root SHA-256 matched exactly. `gfx_prefix_check prepare` on the already relocated frame refused its original capture-directory mismatch. Restoring the descriptor with the bytes lets the checker verify both the original capture identity and the restored location.
3. Evaluated the restored input through `gfx_prefix_check.evaluate` using the retained trusted replay and shader receipt. Result was `FIRST_DIVERGENCE` at event 1, `tex:433`, 23 of 1,930,176 pixels with maximum RGB channel error 1, verified prefix `[0, 0]`, and zero structural problems. The similarly named independent diagnostics replay lacked the authoritative shader binding and returned `INCOMPLETE_EVIDENCE`. Treat the bound trusted run as the restore comparison.
4. Exercised `case_locator publish` and `locate` separately using a newly packaged, private Strategy B diagnostic for the same frame. Discovery found one locator when pointed at the private root. The fresh-root locator verified 1,317 members and 864,558,084 bytes including its manifest, SHA-256 `560f412259c9961e0bb823feb3ad86f1e0c162eee8a073c0c6ac571bed5f181c`. Lookup returned `available`, `verified_all_matched`, and `safe_to_reuse=true`. The historical diagnostic report still carries `graph_status=invalid`. Use the unchanged trusted root and bound receipt above for the comparison.

The default CGL drawable reports Apple OpenGL `2.1 Metal - 91.7`, legacy, Apple M5 Max, 8-bit RGBA with 32-bit normalized depth and 8-bit stencil, and no multisampling. Event 1 targets framebuffer **6**, with RGB8 texture **433** and depth renderbuffer **3**. The drawable record describes framebuffer 0. The selected-draw GL package's supported resource path is framebuffer 0, so a same-seal GL execution control is still absent for this offscreen target. Retain the independent authority registry and its refusal semantics while supporting the actual target. [Exact-prefix report](../../analysis/m7/m7-exact-prefix-report.md) · [GL replay input scope](../../automation/gfx_gl_replay_input.py) · [GL control qualifier](../../automation/gfx_gl_control_qualification.py)

The registered driver, replay-input verifier, and receipt signer matched their reviewed digests. The native GL executor rebuilt with the registry's deterministic recipe and matched its pin, SHA-256 `254618ef8ab232fd1b3c42e6186d642702e714e70d79c92f6d2d908cfa3abc7a`. This verifies build identity. The executable's exactness claim still depends on a qualified target and same-seal observations. [Authority registry and build recipe](../../automation/gfx_gl_control_authorities.py)

The archived precise-default candidate recorded 20 differences on this same seal. R0's pinned native replay recorded 23 under its fast default. The [identity reconciliation](r0-evidence.md#event-1-numerical-identity) verifies both source and recipe identities and preserves the scope of the same-build historical A/B.

### Event-1 observable matrix

| Observable | Original availability and precision | Candidate source and comparison | Qualification gap |
| --- | --- | --- | --- |
| Operation and logical target | Event stream binds draw order, attributes, texture 433 at level 0, framebuffer 6, viewport, dimensions, and post-event writer. IDs are capture-local. | Metal replays the recorded operations and resolves output names and geometry through the independent frame graph. Reconciler checks checkpoint identity. | Replay consumes draw commands. Engine qualification needs independently emitted commands, resource lifetimes, and alias mapping. |
| Vertex, index and uniforms | Draw declarations plus hashed bytes present for the replay inputs. | Native replay loads the same declared inputs and builds Metal buffers and bindings. | No independently measured restored pre-draw GL buffer/binding or whole GL object observation. |
| Program and textures | Program 310, recorded source/bindings and texture data. Compressed DXT1-alpha levels are inputs. | Translator records shader identity. Metal uses the captured resources and its effective recipe and sampler path. | No same-target original-API control for effective linkage or sampled values. Track absent or unobservable resource state separately. |
| Draw state | Records viewport, scissor, cull, blend, color mask, alpha/depth/stencil and raster flags at the draw. | Implemented Metal viewport/raster, depth/stencil and pipeline state are constructed from replay data. The scope checker validates identities and payloads. | Audit candidate effective state through actual queries, including unsupported or lossy fields and state-only regressions. |
| RGB8 attachment contents | Pre-event-0 seed and post-event-0/1 color are sealed. For post-event 1, lower-left BGRA8 readback has defined RGB and zero applicable alpha bits. | Reconciler verifies dimensions, orientation, and bytes, deriving RGB comparison from the sealed attachment. | Exact at event 0, divergent at event 1. An attachment comparison establishes this color scope. |
| Depth and stencil | Identified renderbuffer 3, with float32 depth diagnostic planes. The attachment storage format and size are missing for the draw. Stencil is absent on this target. | Metal can replay depth and emit configured diagnostic observations. The prefix path treats depth and stencil as diagnostics. | Float32 conversion does not demonstrate lossless storage equality. Establish the active attachment's internal format, depth size, and component type before a storage-equivalence claim. |
| Contributing fragment and UV2 | The seal lacks a contributing-fragment record and original UV2. Color change is a lower bound on writes. | Candidate probe can inspect conditional UV2 in an instrumented execution. | Later sparse observations have separate identities and require correspondence, normal RGB change and supported measured depth. No uninstrumented causal numerical stage is qualified. |
| Environment and coverage | Game binary, frame identity, GL profile and default drawable are bound. Asset manifest, macOS build, and per-target storage identity are incomplete as portable qualification identity. | Build and shader receipts bind artifacts. Backend and runtime identity checks have narrower scope than full environment equivalence. | Add only the demonstrated identity fields to the existing case and seal mechanisms. |

The audit verified both later seals, their event streams, referenced blobs and the bound sparse records. Seal `482a6500…` refused at `event1_shader_or_target`. Seal `1efc8880-e438-44aa-9f5b-07a21330c22b` measured renderbuffer 3 as `GL_DEPTH_COMPONENT32` (`0x81a7`), 32 depth bits, with an unsigned-normalized component. It refused its 24-bit sparse control and emitted zero UV2 pixels. Those properties apply to that seal. The smallest next discriminator is an offline Apple GL test of a proven format-matched 32-bit normalized offscreen depth control, including seed and readback precision, before using it to establish fragment correspondence. Keep #278's default-window qualification separate. [Sparse diagnostics and evidence limits](../../analysis/m7/event1-investigation.md) · [Default-drawable follow-up](../../analysis/m7/issue-278-capture-plan.md) · [Native replay state](../../benchmark/eu4_gfx_frame_replay.mm)

### Function oracle

The checked binary's mapped 75-byte body at RVA `0x15ec36a`–`0x15ec3b5` matched the pinned window SHA-256 `8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943`. The local Ghidra index resolved the ABI query. The original x86-64 System V inputs use RDI, RSI, RCX and R9D. Effects are a 4-byte word count and 8-byte argument-pointer role at stride 16, plus the dirty byte at `ctx+0x128`. The reference checks exact changed bytes, preservation outside those writes, and RAX as an observed register. Caller validity beyond the declared slot scope and RAX return-type liveness remain unresolved.

Retain the distinct static, emulation and recorded-original evidence tiers. The 13 hand-specified pilot scenarios and larger frozen Ghidra differential corpus exercise the declared memory region. The historical native fixture reached 64 qualified records and the replay certification is scoped because of missing slots, a missing zero-input stratum and diversity. R3 should expose the existing comparison through a common receipt contract and keep a state-only negative case. The next ABI evidence is caller-liveness and slot-use analysis through the pinned index. [Function contract](../../analysis/reconstruction/_GfxSetConstantBuffer/contract.md) · [Differential harness](../../analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md) · [Native fixture publication](../../results/20261025T155406Z-gfx-live-publication/publication-summary.md)

## DSH and trust boundaries

Preflight passed with the checked Python interpreter and an audit-local copy of the installed profile configuration with existing package links. The primary checkout's default virtualenv lacked NumPy, and DSH could not open its installed `cordis.yml` under the managed sandbox. The audit used an explicit interpreter and a credential-free mirror. `run.sh --workspace … --dump-config` registered only the scratch workspace. The composed workspace overlay disabled `eu4-goal` and retained the native goal driver, tools adapter, and skill. The base campaign composition enabled both EU4 plugins. [Runtime pins](../../dsh/runtime.json) · [Workspace overlay](../../dsh/profile/offline-reconstruction.patch.yml) · [Launcher](../../dsh/scripts/run.sh)

| Concern | Source-grounded finding | Required R4 proof |
| --- | --- | --- |
| Round order | The released, scoped `session/flush` lifecycle listener is the awaited durability extension point. `SessionStore.flush` awaits all listeners and rethrows any rejection. The native round driver awaits it at quiescence before reserving the next round and disarms on error. The existing EU4 bridge's Git snapshot is invoked separately. | Mount a trusted listener and prove a real Git checkpoint after changed rounds, unchanged-round behavior, and fail-closed handling during interruption. |
| Deadline and cancellation | Native goal code has cancellation and round lifecycle. Native bash foreground work receives abort signals. Background jobs can outlive a foreground timeout. Campaign mode seeds an operator deadline and checks active state at expensive entry points. | Demonstrate one enforced wall-clock deadline, cancellation of all owned workers, and recoverable interrupted work in the pinned workspace profile. |
| File and Git isolation | The installed DSH Seatbelt provider allowed writes in both disposable worktrees and a read of a sibling evidence marker. It denied writing the sibling marker. A disposable ref update succeeded when `.git` was inside the standalone workspace and was denied for a linked worktree with Git metadata outside the write root. The fs adapter canonicalizes writes. Keep trusted authority outside the candidate and bind both read and write policy. | Verify terminal, filesystem, native-tool, child, and Git subprocess behaviors together against a sibling trusted checker, reference, and protected refs and credentials. |
| Launch controls | `test_profiles` refuses live work in offline mode. `campaign_guard` checks deadline/mode and launch entitlement. `gfx_launch` and the Metal boot path use the host lease and consume the one-use launch claim at spawn. | Preserve all supported launch choke points, token exclusion, atomic one-use claims, and interruption cleanup before replacing campaign entry points. |
| GPU and validation | The trusted replay paths take the host-wide GPU lock. The prefix checker verifies the copied reference, snapshots candidate sources, builds, isolates candidate execution, and rehashes outputs. The DSH bridge anchors checker and generation inputs. Campaign finalization requires fresh checker execution and a direct goal-completion guard. | Preserve protected required cases, executable and revision binding, independent comparison, fail-closed behavior, and qualification separate from Git preservation. |

**Confirmed on the installed packages.** A disposable Node probe imported the real SessionStore and goal-round driver, installed a scoped flush listener, and drove the native round admission and idle notifications with an in-memory agent. Round 2 stayed unqueued until the test listener resolved. A rejected flush disarmed the goal and subsequent idle notification did not queue round 2. The probe used a deferred marker and did not commit Git, invoke a model, or start an agent process. Source SHA-256 for `dsh-session/src/index.ts` is `8941b4c7a45debf2443b0f937830f806130167e3ef53426833f7225dd5a8bb8f`. For `dsh-goal-round-driver/src/index.ts` it is `fd7a5e4c436f951ca5d236df83a7b0dd662c2ebbffffe49a30714fe55ce4bfe7`.

**R4 design constraint.** Flush is also used around requests and teardown. The listener must be scoped to the trusted session, identify a completed goal round and its current Git state, and be idempotent across duplicate flushes. Separate source checkpoint eligibility from transcript durability. The driver rechecks readiness and waits for descendant work before queue admission. Prove workspace ownership and worker quiescence for the checkpoint itself, including cancellation and child processes. `agent/pre-step` is a later admission defense: downstream `{kind:'reject'}` blocks the goal, while a thrown hook can reschedule the attempt. A passive `agent/status` observer or missing listener cannot enforce the checkpoint. `agent/turn-stopping` is a separate serial normal-turn callback.

**Remaining deployment risk.** Prove trusted plugin composition and privilege in the real workspace profile, including sibling evidence reads, Git writer identity, child containment, and terminal/resume idempotence. Preserve the governed route until those checks pass. [Goal integration](../../docs/dsh-native-goal.md) · [Campaign guard](../../automation/campaign_guard.py) · [Launch and GPU lease](../../automation/launch_lease.py) · [Trusted bridge](../../automation/dsh_bridge.py)

## Section 11 baseline dashboard

These baselines use the pinned source above and explicitly dated historical samples. The [approved dashboard criteria](https://github.com/HagbardCel/eu4-mac-perf/blob/c6ce0082e1755f94a01b6dfe04c8fe22679adc63/plans/repository-refactor/approved-plan.md#11-metrics-and-acceptance-dashboard) govern the later comparison.

| Dimension | R0 baseline | Comparable follow-up |
| --- | --- | --- |
| Authored LOC | 395,033 across six authored responsibilities. [Source baseline](#source-baseline) | Recount by responsibility at R7. Count R0 documentation and enabling work cumulatively, report scientific additions separately, and charge moves zero. [Budget](r0-disposition.md#pr-sequence-and-quantitative-budget) |
| Tracked bytes | 100,115,100 logical bytes, including 82,379,560 generated/data/evidence bytes. This measures the active tree, while historical Git object storage has no R0 baseline. | Compare logical blob bytes with the same policy. Measure Git pack/object storage separately only if claiming storage savings there. Verify unique evidence recovery first. |
| Ownership | One maintained Metal path with live, replay and diagnostic adapters. The selected-draw GL control has a narrow authority scope. Replay, case handling, and workflow wrappers overlap. [Source comparison](r0-disposition.md#source-overlap-and-retirement) | Verify one maintained owner per capability and a clean checkout with public CLI, ABI/build and evidence consumers. |
| Orchestration | One installed native DSH continuation driver. Governed campaigns still bind objective, sidecar, checker and external state. Ordinary workspace composition disables the campaign plugin and retains native rounds. [DSH audit](#dsh-and-trust-boundaries) | Exercise routine native continuation without those per-session campaign inputs, while preserving controlled capture and independent qualification. |
| Startup | After one-time provision/preflight, the workspace path requires choosing the worktree, running `run.sh --workspace`, opening its URL, creating a Web session and submitting a human goal. The governed example additionally supplies sidecar, fresh external deadline/state and a prepared goal with pinned case/checker. No comparable startup duration or context-size sample. [Workspace](../../dsh/README.md#offline-reconstruction-experiments) · [M7 runbook](../../analysis/m7/m7-operator-runbook.md) | From an already provisioned host, count operator actions and supplied artifacts, record seconds to first admitted round and startup context tokens, and verify duration defaults to 120 minutes with optional workstream only. |
| Knowledge retrieval | Earlier M7 tools measured discovery of 53 indexed campaigns at **0.15 s / 8,801 B**, and frame 74 returned **two records / 3,502 B**. Those are locator/read costs. R0 resolved the pinned function ABI and a failed sparse-control result but recorded no matched agent read, token or elapsed totals for the full fact-to-source-and-evidence task. [Measured workflow](../../analysis/m7/efficiency.md#improved-offline-workflow-measured) · [Oracle finding](#function-oracle) · [Sparse result](r0-evidence.md#protected-evidence) | In fresh sessions, give the same two prompts for a positive fact and a failed hypothesis. Require fact, source and verified evidence identity. Count targeted reads, input and cache-read tokens separately, and elapsed time through the first correct evidence link. |
| Feedback speed | Portable full gate **262.183 s**. Native full gate **614.868 s**, with an attributed replicate at **489.736 s**. The authentic case median was **23.667 s cold / 20.691 s warm**. [Measurements and latency status](r0-measurements.md#section-83-latency-status) | Pair before/after invocations at retained scope. Separately compare validation counts, elapsed time and avoidable waits across comparable sessions. |
| Scientific progress | Independent captured-state Metal replay verifies `[0, 0]` on 475, then finds a 23-pixel divergence at event 1. The historical 20-pixel candidate and sparse depth refusal are diagnostic findings. The signed native GL control has no same-target authority for that event. Function evidence comprises static ABI, P-code differential cases and a separate 64-record original publication with scoped replay and outstanding raw recovery. No defensible per-agent-hour qualification rate. [Evidence tiers and results](r0-evidence.md) | For each comparable R6 session, log newly independently qualified transitions/functions/scopes and explained divergences with receipts, coverage and elapsed agent-hours. Report authentic, original-function, emulated and synthetic results by tier, with diagnostic negatives visible. |
| Regression protection | The six 475 replays reproduced the known bound and the checker refused an unbound replay. The full native test run retains one pre-existing compile error. Neither absence of a newly accepted regression nor full renderer equivalence was established across all supported scopes. [Reproduced scope](#reproduced-scope) · [Suite verdicts](r0-measurements.md#offline-suites) | Re-run the required protected regressions and missing/state-only/stale/tampered negatives for promotion, with independent state and receipt checks. |
| Git recovery | Git preserves the pinned start and tracked metadata, and the governed bridge binds candidate snapshots and qualification receipts. DSH flush waits for its listener and a rejection blocks the next round in a disposable probe. No observed population of protected changed-round commits or interrupted/resumed native sessions, so percentage recovery is unavailable. [Lifecycle probe](#dsh-and-trust-boundaries) · [Campaign snapshots](../../docs/dsh-native-goal.md#candidate-snapshots-and-the-qualification-record) | In R4/R6 enumerate completed changed rounds as denominator, verify each protected checkpoint and starting/latest qualified refs, then interrupt and recover in a fresh session. |
| Safety | R0 ran offline with zero EU4 launches. Offline launch entry points refuse authorization and the governed mode has the external deadline, one-use launch claims and host/GPU locks. The workspace probe denied sibling writes and linked-worktree Git ref writes. Full protection of trusted evidence, refs and subprocesses under ordinary workspace mode is unproven. [Trust boundaries](#dsh-and-trust-boundaries) | Reproduce denials, deadline and worker cancellation together on the pinned profile, verify authority after interruption, and audit actual launch receipts. |
| Autonomy | Eight recent exports show **two user messages and three approval interventions**, counted separately by event type. Policy events, setup and actions outside the exports are outside these counts. In one campaign four unnecessary revalidations cost **158.203 s**. Equivalent investigative experiments were not established, so this is repeated validation cost. [Recent trace audit](r0-measurements.md#recent-campaign-audit) | Count setup, messages, approvals and interventions across matched sessions using operator records and exports. Give the resumed agent an existing negative result and compare repeated work by source, input, hypothesis and existing outcome. |

## Reproducibility

Check out `6710a341d864466d7b0ab2d297ab513922d582b6` separately. Keep output in a private scratch directory. For evidence recovery use the existing verifiers and identities in the [protected collection table](r0-evidence.md#protected-evidence).

The private audit root in [pinned inputs](#pinned-inputs) contains `r0-reproduction-6710a341.tar.gz` (11,348 bytes, SHA-256 `0a7e4b87101dab04002336cd0d3d48611236b3141b87b120f68cce0a79b0d1e4`). Recover it from the audit host or request a private copy from its custodian, verify with `shasum -a 256`, and unpack into scratch. It preserves the exact scripts and compact results from the disposable measurements, with no captured game payloads, private session transcripts or installed packages. Retain it with the local audit records.

| Exact artifact inside archive | Recovery or invocation |
| --- | --- |
| `measure-inventory.py` | Original ownership and inventory generator. Its script directory basename provides the Git pin and receives generated output. For an output-free comparison use the recipe below. |
| `attribution-corrected/sitecustomize.py`, `attribution-corrected/run.sh`, `attribute-suite.py` | Exact hook, run wrapper and aggregator. The wrapper expects the pinned clean checkout at sibling `checkout/`. The aggregator reads `summary.json` and `timings.jsonl` from the named run directory. These raw records remain under the local audit root's `attribution-corrected/`; interval file SHA-256 `2fccf6f08da4b82d2fc942bfe400c22d74d294ceaedccfd607ac770bbdfac0b7`. `attribution.txt` in the archive preserves the summarized results. |
| `probe-goal-flush.mjs` and `probe-goal-flush.txt` | Script SHA-256 `9c0476164928b68619761a68506b2afcf441f590c73860ff4a4f5b403ae6c487`. Run `node probe-goal-flush.mjs` using the installed packages identified above and a disposable context. The script imports the real session store and round driver, then resolves or rejects its admitted round's flush. The archive also holds the separate Seatbelt script and its verified result at `dsh-fence/`. |

Run the following from the checkout holding the audit documents and original Git objects. It uses the original audit utilities and reproduces the responsibility and family totals. Each path takes the first matching rule in each classification. It prints counts without writing generated artifacts or reading local captured payloads.

```python
import csv, io, re
from collections import defaultdict
from pathlib import Path
from tooling.common import repo_audit, repo_lifecycle
from tooling.common.repo_snapshot import RepoSnapshot

root = Path.cwd()
pin = "6710a341d864466d7b0ab2d297ab513922d582b6"
snap = RepoSnapshot.from_ref(root, pin)
files, _ = repo_audit.generate(root, pin, False, repo_lifecycle.DEFAULT_POLICY_PATH)
rows = list(csv.DictReader(io.StringIO(files["docs/repo-inventory.csv"])))
doc = (root / "plans/repository-refactor/r0-disposition.md").read_text()
rules = [(k, re.compile(rx)) for k, rx in (s.split(" ", 1) for s in
    doc.split("```text\n", 1)[1].split("```", 1)[0].strip().splitlines())]
generated = {"docs/repo-audit-context.json", "docs/repo-inventory.csv",
    "docs/repo-edges.csv", "docs/repo-lifecycle.csv", "benchmark/submission_counter_schema.h",
    "benchmark/submission_counter_schema.py", "benchmark/subrecord_equivalence_ranges.inc"}
evidence = ("analysis/evidence/", "analysis/output/", "analysis/renderer-corpus/",
            "analysis/held-out/", "analysis/held-out-source/", "results/")
source = set(".py .c .h .cc .cpp .hpp .m .mm .s .swift .sh .js .ts .metal .glsl .vert .frag .java .def .inc .yml .yaml .toml .ini".split())
special = {".githooks/pre-commit", "benchmark/powermetrics_helper",
           "benchmark/power_mode_helper", "dsh/scripts/probe"}
orchestration = set("""
__init__ acceptance agents call_chain campaign_contract campaign_guard cli correction_accept
dsh_bridge dsh_campaign dsh_pilot dsh_profile dsh_receipts dsh_review gfx_contract_accept
gfx_contract_runner gfx_draw_accept gfx_draw_accept_v2 gfx_infra_accept gfx_l32b_complete
gfx_qualification handoff handoff_cli live_gfx m6_live_accept m6_live_capture m7_continuous
next_blocker opencode promote_evidence reconstruction_accept review_result runner session_state
state verification_manifest
""".split())
capture = tuple("""
eu4_gfx_capture_ eu4_gfx_draw_capture eu4_gfx_draw_control eu4_gfx_draw_indexed
eu4_gfx_draw_packet eu4_gfx_frame_observe eu4_gfx_frame_seal eu4_gfx_frame_trace
eu4_gfx_gl_interpose eu4_gfx_live_capture eu4_gfx_observe eu4_gfx_op_
eu4_gfx_set_constant_buffer_capture eu4_gfx_startup_ eu4_gfx_stream_bench eu4_metal_gl_diag
""".split())
data = "Generated and evidence"

def responsibility(path):
    p = Path(path)
    ext = p.suffix.lower()
    if path in generated or set(p.parts[:-1]) & {"vendor", "third_party", "node_modules"}:
        return data
    if path.startswith(evidence) and ext != ".md" and p.name != "trials.py":
        return data
    if ext in {".md", ".rst"}:
        return "Documentation"
    cfg = ext == ".json" and path.startswith(("automation/", "campaigns/", "dsh/", "tooling/"))
    cfg = cfg and (p.name in {"campaign.json", "package.json", "runtime.json", "repo_lifecycle.json"}
                   or path.startswith("dsh/campaigns/"))
    if not (ext in source or cfg or path in special or p.name in
            {"Makefile", ".gitignore", "requirements.txt", "requirements-test.txt", "CMakeLists.txt"}):
        return data
    dossier = path.startswith("analysis/reconstruction/")
    if path.startswith("tests/") or "tests" in p.parts or (
            dossier and p.stem in {"harness", "abi_check", "abi_call6"}):
        return "Tests"
    if path.startswith(("reverse_engineering/", "reconstruction_trace/")):
        return "Reverse engineering"
    if dossier and p.name.startswith("gfx_set_constant_buffer"):
        return "Renderer"
    if path.startswith(("campaigns/", "dsh/", ".github/")) or (
            path.startswith("automation/") and p.name.removesuffix(".py") in orchestration
            and p.name.endswith(".py")):
        return "Orchestration and CI"
    if path.startswith("benchmark/") and p.name.startswith(
            ("eu4_gfx_", "eu4_metal_", "eu4_gl_shadow")) and not p.name.startswith(capture):
        return "Renderer"
    return "Tooling and experiments"

owners = {r["path"]: responsibility(r["path"]) for r in rows}
blobs = snap.read_many([p for p, owner in owners.items() if owner != data], max_bytes=None)
groups = defaultdict(lambda: [0, 0, 0])
responsibilities = defaultdict(lambda: [0, 0, 0])
for row in rows:
    path = row["path"]
    family = next(name for name, rx in rules if rx.search(path))
    text = blobs.get(path, b"")
    text.decode("utf-8")
    assert b"\0" not in text
    lines = text.count(b"\n") + int(bool(text) and not text.endswith(b"\n"))
    for group in (groups[family], responsibilities[owners[path]]):
        group[0] += 1
        group[1] += lines
        group[2] += int(row["size_bytes"])
for dimension in (groups, responsibilities):
    assert tuple(map(sum, zip(*dimension.values()))) == (2388, 395033, 100115100)
for name, counts in sorted(responsibilities.items()):
    print(name, *counts, sep="\t")
for name, _ in rules:
    print(name, *groups[name], sep="\t")
```

## R0 requirement-by-requirement acceptance

R0 authorizes planning and review of the sequenced implementation. The review still owns the disposition decision. Downstream qualification and retirement gates remain part of their proposed PRs.

| Requirement | Acceptance evidence | Remaining condition and effect |
| --- | --- | --- |
| Preserve valid baseline and scientific work (follow-up 1, original R0.1/R0.4) | [Pinned inputs](#pinned-inputs), event matrix, ABI code-window check, and original platform and replay results. | The pre-existing native shader-cache fixture compilation error remains recorded. R2a addresses it before the next native gate. It does not invalidate R0 timing, inventory or retained replay. |
| Disposition and ownership (2, R0.2/R0.3/R0.9) | [42 family rows](r0-disposition.md#subsystem-dispositions) reconcile all tracked files, with consumers, holds, retirement prerequisites and estimates. The [physical map](r0-disposition.md#post-r7-physical-ownership) names the post-R7 owners and existing paths to retain. | External scripts and historical payloads need scoped confirmation at retirement. Holds apply to specific proposed removals, while R1 knowledge and early verified cleanup can proceed. |
| Redundant implementations (3, R0.3/R0.7) | [Overlap table](r0-disposition.md#source-overlap-and-retirement) selects surviving modules and ties removals to regression coverage. | Actual extraction size and equivalence across old case schemas remain PR acceptance checks. |
| Full-suite attribution (4, R0.8) | [Instrumented run](r0-measurements.md#full-native-attribution) assigns 99.98% of unittest wall time to test and fixture groups. The comparison reproduced the original selection, counts and sole pre-existing error. | Native-child internals and exact stage savings require focused R5 measurements. Overlapping wait and subprocess intervals are reported as separate cost views. |
| Agent validation analysis (5, R0.8) | [Recent trace audit](r0-measurements.md#agent-traces-and-repeated-work) ties calls to inputs, source changes, rollback, receipts, test jobs and status waits. | Unidentified executions are excluded from estimated savings. Preserving independent terminal receipts and relevant post-edit gates remains mandatory. |
| Knowledge and function migration (6, R0.4/R0.9) | [R1/R2 coverage](r0-evidence.md#r1-and-r2-migration-coverage) maps the six dossier families, executable modules, proof scopes and negative findings to owners. | Future original behavior and ABI gaps remain scoped scientific work. They do not prevent organizing recovered findings. |
| Protected evidence and 20 vs 23 reconciliation (7, R0.5/R0.9) | [Protected collections](r0-evidence.md#protected-evidence), restored M7 reference, and [source/recipe comparison](r0-evidence.md#event-1-numerical-identity). | M4/M5 manifests and local members and M6 trace blobs verified in place. Separate archival restores, older M7 private cases and the oracle transcripts still gate archival or reuse where applicable. They do not block metadata migration. |
| Native DSH feasibility and governance (8, R0.6/R0.7) | [Lifecycle and trust audit](#dsh-and-trust-boundaries) names the released flush hook and demonstrates failure blocking in a disposable probe. | A real trusted Git checkpoint, cancellation/worker containment and backend isolation are R4 adoption gates. Keep active controls until proven. |
| Quantitative PR and performance budget (9, R0 gate) | The [R1–R7 budget](r0-disposition.md#pr-sequence-and-quantitative-budget) includes cumulative LOC with R0 and keeps the approved phase gates binding. R6 requires seeded repair, continuation and recovery in a subsequent session. | Carry forward measured savings and verified removals. R6 must establish the operating model before routine campaign mechanisms retire. |
| Durable reproduction (10) | The [counting recipe and local archive record](#reproducibility) preserve both classification rules and the exact attribution and DSH probe artifacts. The [tracked plan](https://github.com/HagbardCel/eu4-mac-perf/blob/c6ce0082e1755f94a01b6dfe04c8fe22679adc63/plans/repository-refactor/approved-plan.md) preserves the original gates. | Raw local inputs remain private. Another authorized agent needs the named host or archived originals to reproduce authentic or execution-specific measurements. |
| Dashboard and target calibration (approved §§8.3, 11) | The [dashboard](#section-11-baseline-dashboard) covers all 12 dimensions. The [latency status](r0-measurements.md#section-83-latency-status) and [measurement contract](r0-measurements.md#measurement-contract) separate portable/native and invocation/session costs. | The 125.132 s native spread exceeds the 46–102 s first-wave estimate. The 70% target remains unproven. Measured gaps are assigned to comparable R5 or R6 follow-ups. |
