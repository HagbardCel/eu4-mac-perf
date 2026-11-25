# R0 repository audit

**Status:** audit delivered for disposition and PR-sequence review. [Approved plan baseline](approved-plan.md) · [Dispositions](r0-disposition.md) · [Evidence and migration](r0-evidence.md) · [Measurements](r0-measurements.md)

R0 covers the repository and its verification boundaries. The leading refactor opportunities are to preserve findings and evidence, extract reusable checker behavior from milestone entry points, and measure repeated work before removing tests. A single retained capture establishes an exact RGB match through event 0 and a difference at event 1. Ordinary DSH workspace mode supplies native round continuation. Its filesystem policy and session persistence cover only part of the proposed trust and recovery model.

## Pinned inputs

| Input | Identity or result |
| --- | --- |
| Repository | `main` and local `origin/main` at `6710a341d864466d7b0ab2d297ab513922d582b6`, tree `eeb0c633cefa955900d334882efc8b157681403c` |
| Planning source | [Preserved plan](approved-plan.md), SHA-256 `4b88dc2be8d4f93145d127959267a0a776824c49cb95b00d5cd0b65893f46e86`. The submitted source SHA-256 is `3bede981d415ad92af4239fa86307049de560c485bf3f8fc0f5661b04e317c11`. The tracked copy normalizes Markdown hard breaks and placeholder links in an example. |
| Overlap | GitHub returned zero open PRs at intake, before R0 PR #300. Main already includes #296–#299 numerical recipe identity, event-1 localization, sparse observation, and capture diagnostics. Preserve those paths and the scope of [#278](https://github.com/HagbardCel/eu4-mac-perf/issues/278). |
| Workspace | 71 registered Git worktrees at intake, including historical DSH campaigns and detached verification trees. The primary workspace contained local evidence and an untracked plan. A separate clean checkout pinned each full-suite attempt. |
| Host | macOS 27.0.1 build 26A434, arm64, Xcode 27.2 build 27B52, Python 3.14.10, NumPy 2.5.5. `clang`, `metal`, `glslangValidator`, `spirv-cross`, and Rosetta support files were present. |
| EU4 | Installed GOG macOS x86-64 binary SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` matches the pin. R0 read the file and code window offline. |
| DSH | `0.2.2-rc.5`, Node `v26.13.1`, pnpm `12.23.0`. Installed lifecycle package sources and pinned package identities were available. |
| Local audit outputs | `.local/r0/6710a341d864466d7b0ab2d297ab513922d582b6/`. This directory holds the counting recipe, generated inventory, verification records, and private timing samples. |

The dated status snapshot and the older R0-M profiling experiment have their own revisions and scopes. [Status](../../docs/status.md) · [Target pin](../../reverse_engineering/target.json) · [R0-M profiling experiment](../../docs/r0-measurement.md)

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
| 4 | Shared launch and analysis | `eu4_benchmark` has 46 static callers and `launch_lease` has 27. `eu4_metal_boot` looks like a milestone module but remains an active import from launch, live-Gfx, display, and reconstruction acceptance. The underlying frame-model audit and Phase C evidence verifier still run in CI. [Launch boundary](../../automation/gfx_launch.py) · [Frame-model command](../../benchmark/eu4_frame_model.py) · [Portable CI](../../.github/workflows/profiler.yml) |

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

## Runtime baseline

See [R0 measurements](r0-measurements.md) for the original exact-head baselines, authentic replay pairs, full-suite attribution, analysis of recent DSH traces, and prioritized performance targets.

## Reproducibility

Check out `6710a341d864466d7b0ab2d297ab513922d582b6` separately. Keep output in a private scratch directory. This PR preserves the plan's requirements and gate text. For evidence recovery use the existing verifiers and identities in the [protected collection table](r0-evidence.md#protected-evidence). The installed DSH probe can be reconstructed using `SessionStore.prepare` and `enter` on a new Context, an asynchronous `session/flush` listener, and the released driver's `apply` with disposable goal/agent services. Issue one goal-change notification, mark its queued message claimed and admitted, then send idle. Resolve or reject the next flush and inspect follow-up and disarm order.

Run the following from the checkout holding the audit documents and original Git objects. It uses the original audit utilities and reproduces the family totals from the checked-in selectors. It prints counts and neither regenerates tracked audit artifacts nor reads captured payloads. The original per-path outputs remain local for forensics.

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
def authored(path):
    p = Path(path)
    ext = p.suffix.lower()
    if path in generated or set(p.parts[:-1]) & {"vendor", "third_party", "node_modules"}:
        return False
    if path.startswith(evidence) and ext != ".md" and p.name != "trials.py":
        return False
    cfg = ext == ".json" and path.startswith(("automation/", "campaigns/", "dsh/", "tooling/"))
    cfg = cfg and (p.name in {"campaign.json", "package.json", "runtime.json", "repo_lifecycle.json"}
                   or path.startswith("dsh/campaigns/"))
    return (ext in source | {".md", ".rst"} or cfg or path in special or p.name in
            {"Makefile", ".gitignore", "requirements.txt", "requirements-test.txt", "CMakeLists.txt"})

blobs = snap.read_many([r["path"] for r in rows if authored(r["path"])], max_bytes=None)
groups = defaultdict(lambda: [0, 0, 0])
for row in rows:
    path = row["path"]
    family = next(name for name, rx in rules if rx.search(path))
    text = blobs.get(path, b"")
    group = groups[family]
    group[0] += 1
    group[1] += text.count(b"\n") + int(bool(text) and not text.endswith(b"\n"))
    group[2] += int(row["size_bytes"])
assert tuple(map(sum, zip(*groups.values()))) == (2388, 395033, 100115100)
for name, _ in rules:
    print(name, *groups[name])
```

## R0 requirement-by-requirement acceptance

R0 authorizes planning and review of the sequenced implementation. The review still owns the disposition decision. Downstream qualification and retirement gates remain part of their proposed PRs.

| Requirement | Acceptance evidence | Remaining condition and effect |
| --- | --- | --- |
| Preserve valid baseline and scientific work (follow-up 1, original R0.1/R0.4) | [Pinned inputs](#pinned-inputs), event matrix, ABI code-window check, and original platform and replay results. | The pre-existing native shader-cache fixture compilation error remains recorded. R2a addresses it before the next native gate. It does not invalidate R0 timing, inventory or retained replay. |
| Disposition and ownership (2, R0.2/R0.3/R0.9) | [42 family rows](r0-disposition.md#subsystem-dispositions) reconcile all tracked files, with consumers, holds, retirement prerequisites and estimates. | External scripts and historical payloads need scoped confirmation at retirement. Holds apply to specific proposed removals, while R1 knowledge and early verified cleanup can proceed. |
| Redundant implementations (3, R0.3/R0.7) | [Overlap table](r0-disposition.md#source-overlap-and-retirement) selects surviving modules and ties removals to regression coverage. | Actual extraction size and equivalence across old case schemas remain PR acceptance checks. |
| Full-suite attribution (4, R0.8) | [Instrumented run](r0-measurements.md#full-native-attribution) assigns 99.98% of unittest wall time to test and fixture groups. The comparison reproduced the original selection, counts and sole pre-existing error. | Native-child internals and exact stage savings require focused R5 measurements. Overlapping wait and subprocess intervals are reported as separate cost views. |
| Agent validation analysis (5, R0.8) | [Recent trace audit](r0-measurements.md#agent-traces-and-repeated-work) ties calls to inputs, source changes, rollback, receipts, test jobs and status waits. | Unidentified executions are excluded from estimated savings. Preserving independent terminal receipts and relevant post-edit gates remains mandatory. |
| Knowledge and function migration (6, R0.4/R0.9) | [R1/R2 coverage](r0-evidence.md#r1-and-r2-migration-coverage) maps the six dossier families, executable modules, proof scopes and negative findings to owners. | Future original behavior and ABI gaps remain scoped scientific work. They do not prevent organizing recovered findings. |
| Protected evidence and 20 vs 23 reconciliation (7, R0.5/R0.9) | [Protected collections](r0-evidence.md#protected-evidence), restored M7 reference, and [source/recipe comparison](r0-evidence.md#event-1-numerical-identity). | M4/M5 manifests and local members and M6 trace blobs verified in place. Separate archival restores, older M7 private cases and the oracle transcripts still gate archival or reuse where applicable. They do not block metadata migration. |
| Native DSH feasibility and governance (8, R0.6/R0.7) | [Lifecycle and trust audit](#dsh-and-trust-boundaries) names the released flush hook and demonstrates failure blocking in a disposable probe. | A real trusted Git checkpoint, cancellation/worker containment and backend isolation are R4 adoption gates. Keep active controls until proven. |
| Quantitative PR and performance budget (9, R0 gate) | The [R1–R7 budget](r0-disposition.md#pr-sequence-and-quantitative-budget) estimates −4,230 enabling LOC and counts moves as zero. The [performance budget](r0-measurements.md#optimization-budget) identifies 46–102 s in first-wave full-suite savings against measured costs. | Performance savings are estimates and fall short of the provisional targets. Re-measure comparable workloads before retirement or release. |
| Durable reproduction (10) | [Tracked plan](approved-plan.md), Git and runtime pins, classification recipe and selectors, trace identities, measurement commands and published numerical recipes. | Raw local inputs remain private. Another authorized agent needs the named host or archived originals to reproduce authentic or execution-specific measurements. |
