# EU IV renderer corpus — fixed scope and implementation plan (Stages 1–2)

**Status:** Proposed execution contract, prepared from the user-supplied `eu4-callsite-evidence.zip` (the full 1A census, function inventory, call graph, and provenance). This is a *static, explicit* RVA selection, not an automatic claim to have located every possible graphics-related function.

## Decision and exact scope

Use `renderer-functions.jsonl` as the authoritative list of **4,450 distinct internal functions**, sorted by entry RVA. Use `renderer-entry-rvas.txt` as the decompiler/exporter input. Both are already generated: the implementation must **not re-run selection heuristics** at runtime.

- **3,544 core**: named graphics, backend, map/mesh, resources, rendering-related UI/text, and lifecycle families or specific global graphics entry points.
- **874 direct dependencies**: selected callees of the core functions where graphics relevance, multiple use, or a critical calling path warranted inclusion.
- **32 second-hop dependencies**: shared graphics-relevant callees of direct dependencies.
- **260 deep candidates** in `stage2-deep-targets.jsonl` / `stage2-deep-entry-rvas.txt`: advisory focus for high-P-code, control/data-flow inspection and detailed architecture work; **not** a demand to prove semantics of all 260 before drawing conclusions.
- **44 OpenGL import thunks** in `graphics-boundary-thunks.jsonl`: record as *boundary-only*, not normal decompilation targets. Indirect/dynamically loaded GL functions are not necessarily in this thunk inventory.

Corpus-size estimates from source inventory: **281,654 decoded instructions and 1,145,487 decoded instruction bytes** across selected function records. The selected set owns **31,115 census callsites, including 4,604 indirect sites absent from the original graph**. These are static callsite counts, not hotness or runtime-call frequencies.

### Origin / identity

- Binary SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- Baseline: `auto-v1-20261013`
- Ghidra version in provenance: `12.1.4`
- Image base: `0x100000000`; the manifest stores **RVAs**, not absolute virtual addresses.
- Source evidence archive SHA-256: `ddb59dc222368fa93a2d5659a774580451899e48c74c0fdf5ccf32de4c0d6701`
- Manifest SHA-256: see `manifest-summary.json`. Enforce its exact hash before extraction.
- The source archive is a **static snapshot**. Inclusion based on naming and existing *direct* calls does not establish functional purpose, target identity, or completeness across unresolved indirect calls. Core categories are selection labels, not verified architectural classification.

### Deliverables in this package

| File | Use |
|---|---|
| `renderer-functions.jsonl` | Canonical explicit selection, one function per RVA, full function identity/ranges/callsite annotations |
| `renderer-functions.tsv` | Human-reviewable complete function list; sortable by tier/domain/name/size/missing indirect calls |
| `renderer-entry-rvas.txt` | Flat explicit RVA list for existing Ghidra `ExportDecompilation.java` |
| `stage2-deep-targets.jsonl` | The 260 initial deep-inspection candidates, with exact RVAs and context |
| `stage2-deep-entry-rvas.txt` | Deep-pass RVA list |
| `graphics-boundary-thunks.jsonl` | OpenGL thunk list; API boundary, not decompilation body |
| `manifest-summary.json` | Provenance, scope counts and hashes |

## Stage 1 — Persistent, broad, *read-only* graphics snapshot

### S1.0 — Adopt and qualify the manifest (no Ghidra call)

1. Place this *static* manifest (and hashes) in a new source-controlled scope location, e.g. `analysis/renderer-corpus/scope/v1/`. Do not commit proprietary decompiled bodies or disassembly to a public repository by default.
2. Validate each RVA occurs **exactly once** and resolves to `kind=internal` in the pinned `functions.jsonl`, with exact name, `instruction_ranges`, instruction count and decoded-byte count. Validate there are no duplicate rows or Ghidra import thunks mixed into the decompilation list.
3. Confirm the existing `inventory_bundle_digest`, binary SHA, baseline and Ghidra toolchain identity before exporting anything. If an identity check fails, *stop*; do not silently regenerate inventory or relabel the baseline.
4. Freeze the input SHA; do **not** regenerate the function selection from new source-code rules during this phase. Later extensions require a reviewed `scope-v2` manifest with explicit added RVAs and reasons.

### S1.1 — Qualify exporters on a representative batch (Ghidra, no EU IV launch)

1. Choose roughly 30–50 functions spanning small and large bodies, all key subsystem labels, the nine known priority targets, a UI renderer and a lifecycle function.
2. Reuse the existing qualified imported Ghidra project, `-process <program_name> -noanalysis -readOnly`; **do not use `corpus import`, `corpus inventory` or `corpus decompile` on the full 85,826-function inventory**.
3. Reuse `ExportDecompilation.java` for pseudocode; its existing JSONL includes entry RVA, success/failure/timeout and C-like text. The orchestrator should assert that the returned function entry RVA is the requested RVA (the exporter currently allows a containing-function fallback).
4. Introduce only one new **small read-only static-evidence exporter** for the selected bodies: `entry_rva`, function identity, body ranges, instruction RVA/bytes/mnemonic/operands, instruction-level reference types, Ghidra flow overrides, direct/import references, inferred signature and known globals/data xrefs. Use Ghidra's *function body*, not `[min(entry), max(end)]`, for potentially noncontiguous functions.
5. Validate mixed examples, C++ overloads, multiple body ranges, `CALLIND`, flow-override `JMP`, import thunks and extraction failures on toy fixtures. Record results and resource use; tune only batch size/per-function timeout, not the selection.

### S1.2 — Execute broad extraction (all 4,450 RVAs)

1. Provide one local CLI invocation that runs **restartable bounded batches** (e.g. about 100 RVA entries per batch initially), using the *explicit* RVA list. Each batch launches Ghidra in read-only mode and preserves outputs under the renderer snapshot; the operator should not need to initiate 45 manual commands.
2. For each selected entry, export **complete machine-level evidence** and attempt decompilation. Do not discard a good instruction export when C decompilation fails; persist per-function status (`success`, `timeout`, `failed`, `skipped`).
3. Write batch output to temporary files, validate counts/schema/identity/sorting, and atomically promote into the snapshot directory. Existing identical batches may be reused. Existing different or incomplete published batches should fail closed; provide an explicit repair/archive option, not implicit overwrite.
4. Persist a provenance header: binary SHA, baseline, Ghidra version, project program SHA, manifest hash, exporter script hashes, selected RVA digest, batch ID, and status summary. Do **not** modify `inventory/`, `audits/`, `decompiled/batch_manifest.json`, `index.sqlite` or `provenance.json` for the existing qualified corpus.
5. Keep the files private/local by default: `.local/eu4/<sha>/exports/<rev>/renderer_snapshot/v1/{manifest,static,decompiled,deep,logs}`. If a batch fails, restart *only* the failed batch and preserve completed products.
6. Cover imported OpenGL symbols with a small **boundary table**. Do not attempt to decompile external OS/OpenGL APIs as if they had local implementation bodies.

### S1.3 — Deep semantic extraction (260 candidates; no wholesale manual review)

Use `stage2-deep-entry-rvas.txt` for a second extraction pass. Persist decompiler `HighFunction` / normalized high-level P-code when available, raw P-code for targeted callsite neighborhoods, Varnode definitions/uses, function prototypes/parameter storage, referenced data types and explicitly selected small static-data tables. Label inference as inference. Preserve unresolved or ambiguous pointer chains.

This is still a **bounded export**, not an attempt to serialize Ghidra's complete internal database or prove all indirect targets. For newly identified targets outside scope, log the missing entry RVA as `needs_scope_extension`; do not fabricate a callee or silently rerun the full analysis.

### S1.4 — Snapshot qualification gates

- **Identity:** all original SHA/version/digest checks pass; selected RVA set matches manifest exactly; snapshot claims no inventory changes.
- **Accounting:** exactly **4,450 per-function statuses** in the snapshot and zero duplicates; errors/timeouts represented explicitly, not silently dropped. Successful bodies cover all their Ghidra-listed instructions/ranges or report discrepancies. Reconciliation against the frozen 1A callsites is performed by **instruction RVA**.
- **Coverage:** publish success and failure counts, decoded-instruction and decoded-byte coverage, per-subsystem coverage, exact missing evidence for the 9 priority functions and all 92 `_Gfx*` helpers. Failed decompilation is acceptable if correctly surfaced, but all nine priority anchors must have usable machine evidence before Stage 2 claims their semantics.
- **Determinism:** two runs of a small fixed fixture produce normalized equivalent records; publication idempotence and corrupted-partial rejection covered by unit tests. Timing fields are excluded from content hashes.
- **Conservatism:** P-code `CALLIND` and Ghidra-semantic flow calls remain distinct; do not equate imported thunk symbols to indirectly referenced GL pointers without operand/initializer evidence.

## Stage 2 — Offline graphics architecture reconstruction

Treat the snapshot as a **queryable set of observations**, not an automatically correct recovered source tree. Use simple local JSONL+SQLite/FTS indices only if needed for efficient queries. Prefer extending the existing graph/search utilities rather than creating a new general agent framework.

### S2.1 — Recover the actual graphics boundaries

Start with nine pinned priority functions plus the complete 92 `_Gfx*` helper family, then expand to deep shortlist as questions require. Reconstruct and evidence-link: `CEU3GraphicalMap::Render`, `CPdxMap::Render`, `CPdxMapBorderLayer::DrawFull/DrawBorders`, `CGraphics::RenderBuckets`, `CPdxMeshObject::RenderBuckets`, `_GfxDrawIndexed`, `_GfxSetShader`, `SShaderOpenGL::SetAll`. Compare raw static edges, instruction/SSA def-use and imported GL thunk references.

For each unresolved callsite, classify with exact supporting RVAs and confidence: `confirmed imported GL/function pointer`, `confirmed vtable call`, `callback/other data pointer`, `bounded candidates`, or `unresolved`. **Do not equate a static callsite with execution count or runtime hotness.**

### S2.2 — Build subsystem dossiers (all evidence-links, no premature code patch)

1. **Draw submission/batching:** Draw, DrawIndexed, indexed/instanced variants, border multidraw and mesh buckets; recover branches and call-operand origins.
2. **Shader and fixed-function state:** `SShaderOpenGL::SetAll`, samplers, program selection, uniforms, invalidation and cached state.
3. **Buffers/textures/materials:** creation, binding, updates, data ownership, resource lifetimes, any GL-context affinity.
4. **Frame lifecycle and contexts:** init/shutdown, context switches, frame start/end and presentation boundary.
5. **Map layers/terrain/water/borders:** how paths converge into graphics wrappers and draw calls.
6. **Text/sprites/UI overlays:** where GUI/text rendering shares state and may disrupt batching.
7. **Backend inventory:** imported and dynamically looked-up OpenGL entry points (with evidence of loader and pointer initialization, when available), separate from C++ engine wrappers.

For each subsystem publish a compact architecture document, graph or state transition description, evidence ledger (`entry_rva`, `instruction_rva`, byte/P-code/decomp reference), coverage metrics and **open questions**. Clearly label links as `confirmed-direct`, `proved-indirect`, `possible-indirect` or `unknown`.

### S2.3 — Contract before reconstruction

Choose a tractable low-level function only after sufficient semantic evidence. Record ABI/calling convention, receiver layout and offset confidence, arguments and outputs, state mutations, GL call ordering, failure paths, thread/context assumptions, and a feasible original-vs-reconstructed correctness oracle. Use C++20 as the first faithful target; **do not optimize** until the applicable equivalence criterion is defined.

### S2.4 — Exit conditions

- The graph and data-flow dossiers cover the key graphics boundaries (including `_Gfx*` and render/shader anchors), with unresolved targets explicitly documented.
- The initial graphics frame map, shader/state pipeline, textures/buffers, GUI/text and lifecycle boundaries can be followed from preserved local evidence without reopening Ghidra for routine questions.
- The first faithful reconstruction candidate has a reviewable ABI/side-effect/test contract and a concrete optimization hypothesis, cross-checked against *existing* qualified runtime measurements, not static callsite counts.
- No new EU IV game/profiler runs or paid graphics-agent model calls are required to complete Stages 1–2; neither is a blanket claim that all dynamic pointer targets have been recovered.

## Suggested PR boundaries

| PR | Contents | Gate |
|---|---|---|
| A — Freeze static scope | Explicit manifest, checksums, validation, selected-entry CLI, snapshot schema | Unit-tested identity/duplication/provenance gates; no Ghidra scan |
| B — Broad static + decomp export | Minimal read-only exporter, batched/resumable controller, toy fixture, selected EU IV snapshot | 4,450 status rows and coverage report; no mutation of qualified corpus |
| C — Deep high-P-code export | Bounded 260-RVA deep tier, callsite neighborhoods, normalized def-use, sample dossier | Correct evidence and uncertainty classification for known anchor callsites |
| D — Architecture synthesis | Offline graph/dataflow index, subsystem dossiers, evidence ledger, C++ reconstruction candidate | Explicit architectural confidence and reviewable equivalence-test contract |

**Scope management:** the 4,450-entry manifest is the **planned snapshot foundation**, not a proof that every true graphics function was selected. We cannot enumerate all targets of unresolved indirect calls from the uploaded census alone. New discovered targets should enter an explicit append-only extension manifest and be extracted incrementally, without repeating the broad 4,450-function export. That is the appropriate way to minimize repeated Ghidra work while remaining correct.
