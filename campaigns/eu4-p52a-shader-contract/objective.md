# P5.2a — Shader identity, prerequisites, lifecycle, and consumer contracts

Research-and-contract only on this pinned worktree
(`173321b6d3258576060e7941e9bd91f608e8f543`). Do **not** implement Metal shader
success paths. Do **not** launch EU4. Do **not** edit the binary. Do **not**
change `benchmark/eu4_gfx_shader.mm`, `benchmark/eu4_gfx_shader.h`, or
`benchmark/eu4_gfx_entries.def`.

Load the `opengl-metal-reconstruction` skill before writing analysis. Prefer the
frozen semantic corpus under `analysis/reconstruction/gfx-semantic/v1/`, the
pinned GOG binary (when locally available for offline disassembly only—no
process launch), Decision 0003, and retained metal-init receipts. Label claims
`SDK`, `Apple docs`, `Project-derived`, or `Unresolved`.

Write `analysis/automation/eu4-p52a-shader-contract/note.md` **before** other
analysis artifacts. A line starting with `BLOCKER:` stops the campaign when a
required fact cannot be established offline. Leave a captured research
checkpoint alone; later corrections go under `## Follow-up`. Do not defer an
unresolved required fact to a future reviewer—emit `BLOCKER:`.

## Decision 0003 context (do not over-claim)

The selected draw predicate is the first `_GfxDraw` at caller RVA `0xedfcd1`
(`ShowBasicLoadText` return after `call` at `0xedfccc`) with complete master,
non-null shader, mode 5, effective count ≥ 3.

Historical receipts reached that callsite; `20261102T213704Z` recorded
`effective_count` 0 (`draw_nonvisible`) and is **not** a qualifying Decision
0003 invocation. Identity claims must use evidence levels:

- `static_candidate`
- `statically_determined` (unique for control-flow **and** variant conditions)
- `runtime_confirmed` (forbidden without a qualifying live invocation)

Do **not** assume the Stage D Color effect. `_GfxSetTextures` before `_GfxDraw`
is a texture-state call, not proof of active samplers.

## Required structured artifacts

All under `analysis/gfx-shader/v0/`. Prefer JSON for machine checks; Markdown
contracts may accompany them.

1. `identity-manifest.json` — target effect/shader identity with conditional-
   variant provenance:
   - effect asset (path, hash when recoverable)
   - source generation (`BuildGLSL` branch, macros, includes)
   - variant inputs (settings/capabilities)
   - compiler input (stage-source bytes/hash when reconstructed or observed)
   - metal translation (hash/interface; may be `"planned"` if identity closes)
   - `evidence_level` enum as above
   - If multiple variants remain possible: list them and the unresolved
     selector; do not invent one exact variant.

2. `prerequisite-inventory.json` — shader/effect construction that may need to
   succeed before a normal reach of the selected draw. Each entry:
   `essential` | `deferrable` | `unresolved`, with evidence. Do not assume the
   single target variant is the only one constructed.

3. `lifecycle-disposition.json` — for each of `_GfxCreateShader`,
   `_GfxAddVertexShader`, `_GfxAddPixelShader`, `_GfxLinkShader`,
   `_GfxSetShader`, `_GfxDestroyShader`: disposition
   `replace-metal` | `verified-non-execution` | `BLOCKER`, with RVA and
   evidence. Original Link/Set/Destroy call OpenGL-dependent helpers and cannot
   be presumed safe without disposition. Also note
   `_GfxBuildAndAddVertexShader`, `_GfxBuildAndAddPixelShader`, `BuildGLSL`,
   `SShaderOpenGL::LinkProgram`, `SShaderOpenGL::SetAll`,
   `SShaderOpenGL::UnloadAll`, and the constant-buffer path.

4. `consumer-use-def.json` — for every relevant engine-visible field of the
   `0x78` shader object on the supported path: offset, writers, readers
   (including helpers/aliases), expected value representation, and whether each
   consumer remains original or is replaced. Every reachable consumer must be
   compatible, covered by a replacement, or explicitly blocked. Preserving
   offsets does **not** authorize Metal handles in OpenGL-name fields.

5. `abi-contract.md` — object layout with instruction evidence; unresolved
   fields explicit. Distinguish logical link (engine-visible) from
   `MTLRenderPipelineState` creation (later).

6. `binding-schema.json` — expected attributes, constant buffers, texture/
   sampler slots, and implied blend/depth/raster as a **schema** for P5.2c.
   Runtime engine-owned binds remain P5.3.

## Scope control for later PRs

If the **essential** prerequisite inventory exceeds a bounded, reviewable first
implementation slice, record that explicitly in the note and inventory so P5.2b
can stop-and-reassess. Do not plan a general GLSL→MSL compiler here.

## Editable paths only

- `analysis/automation/eu4-p52a-shader-contract/note.md`
- `analysis/automation/eu4-p52a-shader-contract/progress.md`
- `progress.md`
- `analysis/gfx-shader/v0/identity-manifest.json`
- `analysis/gfx-shader/v0/prerequisite-inventory.json`
- `analysis/gfx-shader/v0/lifecycle-disposition.json`
- `analysis/gfx-shader/v0/consumer-use-def.json`
- `analysis/gfx-shader/v0/abi-contract.md`
- `analysis/gfx-shader/v0/binding-schema.json`
- `analysis/gfx-shader/v0/fixtures/**` (only hashed recovered sources)

Do not commit, reset, or push. Do not edit the campaign directory, the checker,
or automation runner state. No production shader replacement. No new
`GFX_ENTRY`.
