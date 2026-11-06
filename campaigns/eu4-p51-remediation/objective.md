Remediate PR #162 P5.1 findings on this pinned worktree. Do not redesign the intermediate backbuffer. Do not launch EU4. Do not broaden into shaders or draws.

Load the `opengl-metal-reconstruction` skill before editing implementation. Inspect original `_GfxPresent` and `_GfxSetRenderTarget` instructions, Metal drawable/blit/format contracts under `analysis/metal-api/`, and how `SDL_GetWindowSize` relates to `CAMetalLayer.drawableSize` in `benchmark/eu4_metal_boot.m`. Label claims `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`.

Write `analysis/automation/eu4-p51-remediation/note.md` before changing replacement code. Record recovered instruction spans, the Present patch-prefix decision, the frozen dimension/present-coverage policy, and unresolved assumptions. A line starting with `BLOCKER:` stops the campaign when a faithful fix still requires an unsupported guess. Leave a captured research checkpoint alone; later corrections go under `## Follow-up`.

## Findings to fix

1. **Retina / dimensions (P2).** Present must never report success while leaving drawable pixels unwritten. Prefer a drawable-pixel game backbuffer when original evidence supports it. Do not silently shrink the Metal layer to `SDL_GetWindowSize` without that evidence. Until the mapping is resolved, fail closed before submit. Require destination size and `BGRA8Unorm` agreement with the frozen policy.

2. **`_GfxPresent` prefix (P2).** The registered prefix `55 48 89 e5 48 8b` ends inside a `mov`. Recover complete first instructions from the pinned executable. Register only a complete-instruction prefix. If no complete boundary fits the existing 5–8-byte patcher, emit `BLOCKER:` or a narrowly justified alternative documented in the note (for example raising the patcher max for the complete 11-byte prologue). Do not call a mid-instruction prefix instruction-complete.

3. **Production wrappers (P3).** Exercise successful `eu4_gfx_set_render_target` and `eu4_gfx_present` (return `1`) on a supported path in `tests/gfx_default_target_host.mm`.

4. **Failure tests (P3).** Unsupported destination dimensions and pixel formats must preserve `gameContentsKnown` / `readyForPresent`.

Keep the injected-texture (not live `CAMetalDrawable`) limitation in the contract.

## Editable paths only

- `analysis/automation/eu4-p51-remediation/note.md`
- `progress.md`
- `benchmark/eu4_gfx_init.h`
- `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_gfx_entries.def`
- `benchmark/eu4_gfx_entry_patch.h`
- `benchmark/eu4_gfx_entry_patch.c`
- `tests/gfx_default_target_host.mm`
- `tests/test_gfx_default_target.py`
- `tests/test_gfx_entry_registry.py`
- `tests/metal_boot_host.c`
- `analysis/gfx-render/v0/default-target-contract.md`

Do not commit, reset, or push. Do not edit the campaign directory, the checker, or automation runner state. This worktree is not a sandbox: shell commands run as the same OS user as the runner.
