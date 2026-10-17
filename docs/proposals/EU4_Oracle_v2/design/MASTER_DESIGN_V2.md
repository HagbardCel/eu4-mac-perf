# EU IV general-purpose behavioral oracle v2

**Design status:** implementation blueprint, NOT an EU IV runtime capture, proven function equivalence, or a validated OpenCode end-to-end integration. Updated 2026-10-16.

**Baseline:** `auto-v1-20261013`; GOG EU IV 1.37.5 macOS x86-64; executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

## Executive decisions

1. Execute a repository maintenance PR-0 **before** integrating the new oracle. Preserve immutable evidence paths and CI semantics.
2. Use **one primary OpenCode reconstruction agent**. Inspection, replay, compilation, differential comparison, certification, iteration control, and budgets are deterministic tools/services, not other agents.
3. Keep the agent-facing contract independent of OpenCode: stable versioned RPC/CLI operations implemented in Python initially; TypeScript custom tools are thin adapters. Pin an OpenCode release and SDK to one tested API generation.
4. The first real-game launch is a multi-epoch acquisition that records **generic behavioral evidence** plus an independently replayable graphics workload, with strong count/novelty deduplication and explicit coverage gaps.
5. The collector **always distinguishes exact invocation totals from deep-captured observations**. A cheap fingerprint is not sufficient to label a call a behavioral duplicate. Ground-truth bytes, unknown dependencies, and capture loss are never silently hidden.
6. Differential diagnosis must localize first observable divergences in typed/raw state, effects, ABI, or resource semantics. Comparison and certification use independent deterministic code.
7. Faithful x86-64 C++ reconstruction is the initial target. ARM64/native Metal follow only after portable semantics, lifetimes, synchronization, and graphics replay are validated.

## Evidence and what it does *not* prove

The attached full-program corpus holds approximately 85,826 indexed functions, 84,770 successful pseudocode bodies, 544,566 direct call edges and a sealed, 4,450-function renderer snapshot; neither static compilation coverage nor a large trace proves general equivalence. The full program has unresolved indirect dispatch and hidden mutable state. Only a runtime-qualified trace can provide selected behavioral oracle fixtures, and finite fixtures establish **observed equivalence**, not universal proof.

## Architecture and trust boundaries

```text
STATIC SOURCE (read-only)                  ORIGINAL EU IV (operator-controlled)
  Ghidra SQL + sealed instructions            verified Mach-O/ABI instrumentation
  function IDs / code hashes                   + global/resource state adapters
  xrefs / provisional layouts                  + 3 separate acquisition intensities
            |                                          |
            +----------------+-------------------------+
                             v
                  IMMUTABLE CAPTURE STORE
              append-only event shards + receipts
              content-addressed payload blobs
              SQLite functions/fixtures/index
              exact counts + coverage + uncertainty
                             |
             read-only, small bounded responses
                             v
               ORACLE SERVICE / TOOL ADAPTER
                  inspect, fixtures, coverage
              compile, compare, diff, certify
                  capture-gap ticket (only)
                             |
                    ONE OPENCODE AGENT
               inspect → edit C++ → test → fix
                             |
                   EXTERNAL CONTROLLER
              session/steps/tokens/cost/timeout
                retry budget + deterministic gate
                             |
             isolated candidate worktree and sandbox
```

**Security boundary:** Do not trust an LLM prompt or OpenCode tool permissions as an OS sandbox. The agent runs in a restricted worktree, with no original binary, game launcher, writable golden blobs, or arbitrary network/host shell. A trusted test executor compiles/loads potentially incorrect native code **in a separate isolated process**, with resource/time limits. Oracle tools validate IDs and paths before any subprocess execution.

## PR-0: repository cleanup based on observed repository layout

On 2026-10-16, the public `HagbardCel/eu4-mac-perf` tree contained 808 entries (not necessarily 808 source files): `results/` 243, `tests/` 205, `analysis/` 143, `benchmark/` 112, `docs/` 50, `reverse_engineering/` 32. The repository's own root README already labels `benchmark/` and `analysis/` **legacy** and targets `tooling/measurement`, `tooling/instrumentation`, `experiments/`, `measurements/derived`, and `reverse_engineering/reports`. `docs/status.md` explicitly closes Phase C intrusive profiling, while keeping R-mode evidence and limited follow-on measurements. `analysis/border-track-wind-down.md` closes the border mutation track after an empty mode-0 domain in the paused Venice fixture, while retaining static and parity evidence.

**Cleanup method, not indiscriminate file deletion:**

1. Generate a manifest of tracked paths, file types, sizes, imports, CLI entrypoints, test ownership, CI references, documentation links, and sealed-result hash dependencies. Label `ACTIVE`, `SHARED_DEPENDENCY`, `FROZEN_VERIFIER`, `RETIRED_EXPERIMENT`, `GENERATED_DERIVED`, `UNKNOWN`.
2. Identify canonical CLI wrappers and preserve them through adapters until all users/CI migrate. Keep `reverse_engineering` full-corpus pipeline and sealed snapshot validators live.
3. Archive truly retired experimental implementation under explicit `archive/` or stable Git tags; move only when tests, hashes, historical links and signed/hashed manifests remain valid. Leave sealed capsules at their original paths by default.
4. Preserve prior negative results and their provenance: particularly border mode-0 census, draw-cost comparison and Phase C non-qualification. Make stale docs refer to canonical `docs/status.md` and strategy.
5. Publish a manifest of all moves, redirects, retained interfaces and test evidence. Delete no library whose consumers are unclassified.
6. Make PR-0 purely organizational; no new EU IV run; require full test suite, evidence validation, and no regression in CLI outputs.

**Recommended logical layout** (migrate incrementally): `reverse_engineering/`, `tooling/instrumentation/`, `tooling/measurement/`, `oracle/{schema,store,capture,dedup,compare,service}`, `reconstruction/{cpp,runner,agent}`, `experiments/`, `docs/`, `tests/`, and immutable `results/`. Unpublished full binary/corpus stays in `.local/`.

## Generalized observation model

Let a function invocation be:

`(return, post_state, external_effects, exit_status) = F(arguments, pre_state, environmental_inputs)`.

A fixture comprises:
- Provenance: `(module SHA, RVA, code hash, hook-site hash, acquisition policy hash, capture version, scenario, frame, callsite, thread)`.
- **ABI carriers:** validated integer/XMM register and stack args, `this`, hidden sret, return carrier, exception/unwind status, integer/float bit widths. Avoid meaningful void-return values.
- **Identity:** stable `object_id@generation`, `resource_id@generation`, recorded pointer alias graph and backed address-range extents. Raw addresses are retained privately as diagnostics, not used for relocation-independent equality.
- **Pre-state:** typed fields where ABI/layout confidence permits, *and* exact raw bytes/length; declared global/TLS reads, clocks, RNG or callback inputs, if instrumented.
- **Post-state:** raw byte spans, field/write delta with declared valid/unknown masks, memory and allocation effects; exceptions and exit codes.
- **Ordered effects:** GL/GLEW calls, resource changes, SDL/OS calls, callback returns, allocation/free; correlation across multi-call transactions and parent/child spans.
- **Fidelity flags:** read/write/effect sets verified? Capture truncated? Lost events? Are ambient dependencies unknown? Any nondeterministic concurrency? Every omission is explicit.

**Static evidence is never conflated with runtime certainty.** A decompiler guesses a field, but raw bytes establish the actual recorded observation.

## Deduplication: exact counts, rich unique fixtures

Maintain separate ledgers:

- **Exact function counters:** per-function/per-thread/per-epoch 64-bit counters incremented on every successfully intercepted entry. Reconcile with entry/exit counts and overflow; interception coverage remains distinct from whole-program execution coverage.
- **Encounter ledger:** broad, cheap fingerprints of ABI carriers and known relevant state, plus callsite, scenario and phase. Counts by bucket. This groups *candidate duplicates*, not proven equal behavior.
- **Deep observation journal:** pre-state and output/effect data for policy-selected candidates; bounded buffers preserve observation or record explicit loss.
- **Canonical postprocessing:** compute a canonical semantic digest after relocating pointer identities, applying *reviewed* masks to known padding/uninitialized data, including state and side effects. Compare actual normalized contents on hash collision. Dedup only if observable semantics + declared dependency capture match; preserve counts and provenance for repeats.

`semantic_digest = SHA256(schema_version || function_id || ABI || normalized_args || observed_pre_state || return || observed_post_state || normalized_effects || fidelity_policy)`.

Beware: a repeated **input fingerprint** may still yield a new **output**, because of hidden state; keep periodic spot checks and capture-unseen-state triggers. Conditional skip means **not deeply observed**, not a proven duplicate. Track observed repeats separately from counted-but-unsampled invocations. Additional coverage on branch signatures, new dynamic targets, aliases, error returns, state-transition classes and lifecycle generations is more valuable than many identical paused frames.

**Storage profile:** versioned length-prefixed binary event records in preallocated per-thread ring buffers, async writer, durable segment hash receipts, content-addressed blob store, SQLite catalog and small JSON projections. Do not compress/hash/serialize complex objects on the GL submission thread. Resource payload bytes are necessary for graphics replay; fingerprints are not enough.

## One process, three acquisition intensity modes

1. **COUNTERS:** fast counters for frame/pass/callsite, GL calls, bytes uploaded, draw modes, resource churn, and estimated work, with no per-invocation logging. This supports performance attribution (relative to existing R-mode baselines, accounting for instrumentation bias).
2. **DISCOVERY:** broad but bounded indirect-call target census, ABI/return-carrier sampling, object/resource lifecycle for approved families, allocation identities, provenance and scenario events. Coverage-limited; not automatically replayable.
3. **SEMANTIC + GRAPHICS:** resource-complete graphics-frame checkpoint; complete ordered commands/effective state for selected frames; pre/post state/effects for priority function families. Trace overhead is permitted, but timing is excluded from perf conclusions.

Suggested one-process sequence: INIT + resource census → counter epoch A → reference frame F1 → interaction/hover/select F2 → camera/zoom F3 → map-mode F4 → UI/text F5 (prioritize 3–5 replayable frames, not five partial frames) → targeted semantic variants and (optional) controlled simulation tick → counter epoch B → post-run sealing. Restore consistent scenario conditions when comparing A/B, and record all differences.

**Capture policies by function family:**
- Gfx small setters, constant-buffer operations, vertex/index state, shader binding: ABI + exact pre/post slices and GL-effect transcripts. Distinct equal/switch/no-op/dirty transitions.
- Renderer loops (borders/terrain/meshes/text): high-level ordered draw packets, input lists and key game-state reads (bounded pre-state); scene-level fixtures may need additional reconstruction.
- Resource and font allocation/update/destruction: full used content and generations over a replay frame, even if created before the frame.
- General non-graphics functions: generic typed fixture adapter can be enabled in a subsequent acquisition; discovery coverage alone is not a unit oracle.

**No game-run gate** until verified instruction-site checks, Darwin x86-64 calling convention and stack/XMM preservation, recursion/reentrancy, concurrent logging, allocation exceptions, buffer overflow, GL/GLEW dispatch (not only imports), fully independent synthetic replay, and provenance sealer pass offline. Hook placement may require binary-specific adjustments.

## Replayability and evidence levels

`DISCOVERY` → `ABI_CAPTURED` → `STATE_CAPTURED` → `EFFECTS_CAPTURED` → `REPLAYABLE_UNIT` → `REPLAYABLE_GRAPHICS_FRAME` / `REPLAYABLE_SCENE`. Each stage has machine-checked gates; missing state sets `INCOMPLETE_EVIDENCE`, never pass. A captured graphics trace can certify Gfx frame replay without proving scene/game-state separation.

**Correction to simplistic equality:** First faithful reconstruction compares exact observable bytes, logical aliases, exits and ordered effects *where evidenced*. Optimized OpenGL or Metal code may deliberately change calls; its comparator must compare **effective rendering semantics and pixels** under explicit fidelity/tolerance policies, not identical GL calls.

## Structured diagnostic protocol

Every candidate test must yield one of:

`PASS`, `MISMATCH`, `UNREPLAYABLE`, `INCOMPLETE_ORACLE`, `CANDIDATE_CRASH`, `CANDIDATE_TIMEOUT`, `HARNESS_ERROR`, `INCONCLUSIVE_NONDETERMINISM`.

For `MISMATCH`, report: fixture ID and hash, function and candidate build ID, *first observable divergence* as `domain` (`ABI`, `return`, `memory`, `effects`, `exception`, `resource`, `visual`), logical object + byte offset/field (confidence), expected/actual typed and raw bits, policy comparator, nearby causal context and provenance. Include full-failure paths on demand, not by default. For memory: compare only valid declared regions and label unobservable; track relocated aliases. For external effects: compare ordered operations, args, optional timestamps for debugging, effects of mocked nested callees. For FP: exact-bit vs configured ULP/tolerance and NaN/negative-zero handling; never default to fuzzy.

**Root cause ≠ first divergence.** Provide evidence-backed suspects and static disassembly locations only as hypotheses. Preserve artifacts needed for later interpretation and diagnosis. A green fixture with unsampled side effects is not certification.

Example agent feedback:

```json
{"function_id":"gfx:0x15ec36a","fixture_id":"fx-024","status":"MISMATCH", "first_divergence":{"domain":"memory","object_id":"GfxContext:1@g0","offset":296,"field":"dirty_flag","field_confidence":"inferred","expected_hex":"01","actual_hex":"00"}, "capture":{"replayable":true,"effect_log_complete":true,"dropped_events":0}, "context_refs":{"raw_pre":"sha256:...","raw_post":"sha256:...","static_disassembly":"rva:0x15ec36a"}}
```

Values are **illustrative**, not a recorded EU IV fixture.

## One OpenCode agent and one deterministic control plane

Pin a documented/installed OpenCode API generation (current v1 docs show `agent` singular, `permission` object, `.opencode/agents/`, `.opencode/tools/`, `@opencode-ai/sdk`; experimental/parallel V2 docs use different shapes—**do not mix these without a migration test**). Use a project-local **primary** agent `eu4-reconstructor`, not multiple role agents. Disable `task` to prevent subagent delegation. The controller manages one session per target function (rather than one enormous, compaction-heavy session for the whole corpus), checkpointing candidate code and attempt state across sessions.

Tool API (defined as thin wrappers, not built-in OpenCode features):

- `oracle_inspect(function_id, sections)` — bounded disassembly/pseudocode/ABI and xrefs, with provenance.
- `oracle_fixtures(function_id, selection)` — representative training fixtures, coverage and known blindspots; no hidden expectations.
- `oracle_coverage(function_id)` — capture adequacy, novelty strata, exact total/observed/unique count.
- `oracle_test(function_id, candidate_path, fixture_subset)` — compile and execute candidate in sandbox against visible fixtures.
- `oracle_diff(test_run_id, max_events)` — first divergence, expected/actual raw and typed state, optional causal window.
- `oracle_certify(function_id, candidate_path)` — immutable full/held-out suite + static/ABI/sanitizer gates; only trusted controller can accept certification.
- `oracle_capture_gap(function_id, evidence_needed)` — ticket only; does not launch game.

OpenCode SDK manages `session.create`, `session.prompt`, `session.messages`, `session.abort` and events as documented. Agent permissions deny OS shell, web, external directories and subagent calls; allow file editing only in isolated candidate worktree plus oracle tool methods. Policy check by a trusted executor is mandatory; OpenCode permissions are **not a hard security boundary**. `steps` limits prompt-local tool use, but controller budgets across sessions and attempts are authoritative.

Iteration: choose eligible function → inspect ABI/static + fixture completeness → write minimal C++ → `oracle_test` → `oracle_diff` → repeat within budget → `oracle_certify` → status `CERTIFIED_FOR_CAPTURED_DOMAIN` or `BLOCKED_WITH_REASON`. Never equate passing finite fixtures to proof over unseen states. Use separate x86-64 C++ semantics profile first; ARM64/compiler-optimization cross-target equivalence is a later milestone.

A deterministic controller enforces token/cost caps, walltime, number of trials, stable session IDs, tool rate limits, and records a SHA-addressed `attempt_receipt` per edit/test, including candidate source digest, fixture corpus version, compiler flags, crashes, coverage and confidence. Provider billing limits may be post-response, so costs are conservatively budgeted and **not described as an absolute bound**.

## PR sequence and gates

| PR | Work / deliverable | Non-negotiable gate |
|----|--------------------|---------------------|
| **PR-0** | Repo audit, path ownership, consolidate true duplicates, archive retired experiments without corrupting evidence | Existing CLI, CI and immutable-capsule validation unchanged |
| **PR-A** | Versioned schema, content-addressed blobs, counts, coverage, fixture/candidate index, structured diff format | Roundtrip, schema migration, collision, tamper, truncation tests |
| **PR-B** | Generic recorder core: counters + novelty sampler + pre/post/effects with bounded serialization | Synthetic ABI/alias/concurrency/recursion/overflow tests |
| **PR-C** | Version-pinned hook packs for Gfx/scene, GL/GLEW, SDL, resources | Byte-verified site, trampoline safety, no behavior perturbation in synthetic binary |
| **PR-D** | One-launch epoch coordinator, scenario controls, sealer, completeness and replay preflight | Synthetic multi-epoch rehearsal, resource closure, enforced quotas |
| **PR-E** | One **operator-authorized** EU IV acquisition, without LLM in control of launcher | Valid provenance, counter epochs, replayable frame(s) or explicit failure, no false positive gate |
| **PR-F** | Oracle service, OpenCode custom tools, compiler sandbox, structured differential reports | Offline all-tools smoke, no live game dependency, sandbox and negative tests |
| **PR-G** | Single-agent loop, controller and unbiased evaluation with held-outs | Pilot cohort completion, budget/stop enforcement, reproducible receipts |

**Suggested sequencing improvement:** Build the offline `oracle_test`/`oracle_diff` and a minimal OpenCode stub-agent smoke test **in parallel with PR-A/B**, using synthetic fixtures. Do not postpone verifying OpenCode tooling until after the expensive acquisition. PR-F then hardens and scales a demonstrated integration.

## Evaluation and success criteria

- **Acquisition:** exact hooked-entry count == sum of bucketed + unclassified entries (or explicit overflow); deep-captured vs unique vs sampled vs dropped totals reconcile; same hash means same canonical payload, not merely same input; resource closure and provenance hashes validated.
- **Oracle:** fixture is replayable or carries explicit gaps; isolate candidate crashes without damaging corpus; report first field/effect divergence deterministically.
- **OpenCode:** exact observed calls to authorized tools, a working single-agent edit/test/retry cycle over a synthetic C++ function, restricted executable/environment, and provider model swap tests.
- **Reconstruction:** selected Gfx setters meet captured-domain E0/E1 gates and unseen held-outs; recorded `INCONCLUSIVE` when coverage limited; compare workload/cost per function and model. No auto patches into original EU IV.
- **Performance:** counter-mode results are reconciled with baseline methodology; never infer frame improvements from static draw counts or heavy semantic trace timings.

## Risks and contingency

- **Hook ABI incorrect:** non-invasive synthetic qualification first; pause/omit affected hooks. A crash during acquisition cannot be patched by postprocessing.
- **Missing state:** `INCOMPLETE_ORACLE` with explicit pointers/global dependencies. More bytes without relevant causal state do not solve the problem.
- **Dedup semantic collision:** verify canonical payload equality, preserve representative samples; keep hash collisions distinguishable.
- **Resource checkpoint missing:** cannot claim frame replay, preserve miss receipts and fall back to function-level fixtures.
- **OpenCode API churn:** pin the version, run contract tests on upgrades, isolate via CLI/SDK adapter.
- **Tool security:** deny agent shell, sandbox native code; enforce file scope in runner, not through prompts.
- **Cheap model failure:** benchmark on pilot; switch models via OpenCode configuration, not a new architecture.
- **One-launch ideal fails:** finite dynamic coverage is inherently limited; preserve complete artifact + specific next-acquisition gaps, avoid assuming a second run is forbidden forever.

## Verified external API references (2026-10-16)

- Custom tools: https://docs.opencode.ai/docs/custom-tools/
- Agents: https://docs.opencode.ai/docs/agents/
- SDK: https://docs.opencode.ai/docs/sdk/
- Permissions: https://docs.opencode.ai/docs/permissions/
- CLI: https://docs.opencode.ai/docs/cli/
- Note: `/v2/docs` pages are separate and may have a different schema; select one supported configuration generation.
