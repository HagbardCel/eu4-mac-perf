# PR-0 ... PR-G delivery and acceptance checklist

Every stage is separately revertible and backward-compatible until the previous interfaces are explicitly retired. Baseline is version-pinned. This document defines reviews; it does not claim any PR has been implemented.

## PR-0 — Repo consolidation

- [ ] Audit Git-tracked path inventory, `import` graph, entrypoints, CI dependencies, documentation links, generated files and evidence paths.
- [ ] Label active/retired/frozen/dependency/unknown; map current legacy `benchmark/`, `analysis/`, `results/` to future owners.
- [ ] Preserve Phase C sealed capsule at original location and re-verify all receipts.
- [ ] Border experiments moved only if `analysis/border-track-wind-down.md` references and parity tests remain discoverable.
- [ ] Preserve `reverse_engineering` corpus commands and `benchmark/eu4_benchmark.py` compatibility interface while migrating.
- [ ] No EU IV run, no new performance conclusions; CI regression and `git diff --check` green.

## PR-A — Storage, schema, comparator contracts

- [ ] Stable `(binary, module, RVA, code hash)` function IDs, immutable provenance and versioned fixture IDs.
- [ ] SQLite catalog, CAS bytes, event segments and atomic sealer. Byte digests checked on load.
- [ ] Exact encounter ledger, sampled and incomplete counts, deep complete unique and verified-repeat reconciliation.
- [ ] Structured comparison status/error taxonomy and first divergence, explicit memory masks/unknowns.
- [ ] Cross-version schema upgrade test; adversarial digest collision and nondeterministic-field tests.

## PR-B — Generic recorder

- [ ] Non-allocating entry count path; bounded optional ABI/pre/post/effect capture; thread-local buffers.
- [ ] Pre/post typed serializers require verified size/length bounds and pointer readability; encode raw bits.
- [ ] Alias and object-generation tracking, parent/child span IDs, cross-thread causality caveats.
- [ ] Threshold/novelty sampling for diverse inputs, states, error returns, dispatch targets.
- [ ] Synthetic tests: nulls, callbacks, recursion, exceptions, register args/XMM, sret, aliases and buffer pressure.

## PR-C — Verified hooks

- [ ] Machine bytes, trampoline ABI and deterministic hook disable for pinned x86-64 binary.
- [ ] Direct GL imports **and** GLEW-dispatched GL functions, SDL, shader/resource lifecycle.
- [ ] Synthetic hook-on/hook-off output identity, GL behavior parity and reentrancy/exception safety.
- [ ] Unknown/unsupported hook signatures omitted and recorded; no forced unsafe expansion.

## PR-D — Coordinator/qualification

- [ ] Scenarios and explicit epoch state machine; counter A/B not mixed with heavy capture.
- [ ] Startup resource census plus selected graphics-frame resource closure.
- [ ] Backpressure and capture quota failure mark non-certifiable evidence, never silently discard.
- [ ] Complete synthetic rehearsal including unexpected process exit and interrupted artifact sealing.
- [ ] OpenCode three-tool synthetic smoke done now, to avoid tooling surprise after the expensive game run.

## PR-E — Authorized one-process game capture

- [ ] Operator explicit launch authorization, save and settings hash, source code/toolchain identity.
- [ ] Protected baseline counters, several staged scenario transitions, one or more fully replayable frames where achieved.
- [ ] All counters reconcile across epochs and states; post-game crypto receipts.
- [ ] No claims that uncovered functions or unseen inputs now have an oracle.

## PR-F — Service/OpenCode tools

- [ ] Pin OpenCode CLI, plugin API, SDK and models; version-generation compatibility tests.
- [ ] Typed oracle inspect/fixtures/test/diff/coverage/certify/gap tool contracts.
- [ ] Separate native code executor with OS resource and filesystem/network restrictions.
- [ ] Query limits, redaction and held-out isolation; reject path traversal and suspicious tool args.
- [ ] Regression fixtures generate memory and ordered-effect failures with deterministic first divergence.

## PR-G — Single agent loop

- [ ] One primary agent; subagents disabled by config and effective permissions.
- [ ] Controller aborts on time, requests/cost, iterations, no-progress or missing evidence.
- [ ] Repeatable pilot cohort across simple setters, shader state and draw submission functions.
- [ ] Held-out certification performed separately; passing test fixtures != universal proof.
- [ ] Compare free vs strong reference model with identical oracle/tool budget; store tokens, costs and retries.
