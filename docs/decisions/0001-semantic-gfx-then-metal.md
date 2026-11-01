# 0001 — Semantic `_Gfx*` reading, then Metal

**Status:** accepted 2026-11-01. Immutable. Supersede with a later record.

**Scope:** pinned GOG EU IV 1.37.5 x86-64. This decision does not authorize a live process modification, an EU4 launch, or a new export.

## Decision

Read the known `_Gfx*` surface into one semantic model, then implement that model in Metal in execution order. Do not build an OpenGL compatibility layer, and do not render with both APIs in one process.

The inventory is the 92 `_Gfx*` rows in the frozen renderer scope (4,415 instructions). Identity is the entry RVA. That inventory is the reconstruction set. It is not, by itself, the set of functions Metal must replace.

Progress after this reading is a runtime milestone: valid Metal `_GfxInit` postconditions, then one real Metal frame. It is not a count of additional documents.

## Sequence

1. **Semantic reading.** For each of the 92 functions, record the behavior the existing sealed evidence supports. Mark the rest unknown. A function need not compile, reach `reconstructed`, or pass an equivalence test.
2. **Metal strategy.** Define a Metal implementation for functions whose migration role is `replace-metal` or still `undetermined`. `retain-original` means the original function can stay, and only while its data layout and calling contract stay valid. It is not a proof that the function is independent of OpenGL.
3. **`_GfxInit`.** Replace initialization with real Metal resources and the engine-visible postconditions its callers require. Use only the external dependencies that init demonstrably needs, including the separate GLEW call in `_GfxGetGraphicsSettings`. B0 remains the last artificial startup checkpoint. Do not add another one.
4. **Execution order.** Run with that init. When an unmigrated OpenGL-dependent path is reached, stop, implement that function together with the cohesive dependency it needs, and patch the original entry. Continue until one frame is presented without an OpenGL rendering call.
5. **Coverage, then cost.** Check menus and a running map against separate OpenGL reference captures. Only after that, apply the batching and pipeline-cache work already identified by measurement.

OpenGL interception stays a fail-fast check. It is not a translation layer.

## Out of scope until a Metal frame exists

Another function census, another bulk decompilation, resolving all 126 indirect sites in advance, a general OpenGL-to-Metal translator, mixed OpenGL/Metal rendering, B2-style draw capture as a gate, and mandatory oracle, TR, or GEN coverage for every replacement function.

PR #126 is not extended. PR #132's `_GfxDrawIndexed` observations remain evidence. Further live capture is not an implementation gate. The existing `_GfxSetConstantBuffer` reconstruction and `_GfxForceWireframe` contract stay where they are. This decision does not rebuild them.

## What this does not change

Historical oracle receipts, qualification limits, and safety constraints stay valid. Their gates are not prerequisites for a semantic dossier or for a later Metal-native implementation. A later Metal patch is what authorizes a live process change. This record does not.

The reading itself is [analysis/reconstruction/gfx-semantic/v1/README.md](../../analysis/reconstruction/gfx-semantic/v1/README.md).
