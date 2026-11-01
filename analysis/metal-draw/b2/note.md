# B2 — one real EU4 draw on Metal

B2 is incomplete. The one authorized EU4 session did not install a callsite patch, so no original-input fixture was frozen and no EU4 pixels were compared.

## Callsites

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Offsets are image-relative (`callsite_offset` against `image_base` `0x100000000` in [`analysis/draw-callers.json`](../../draw-callers.json)). In this Mach-O, `__TEXT` has `vmaddr` `0x100000000` and `fileoff` 0, so these three RVAs are also file offsets. The capture still adds the RVA to the loaded image header.

| Site | RVA | Bytes | Decoded target |
| --- | --- | --- | --- |
| Mesh `GfxDrawIndexed` | `0x14c8404` | `e86e261200` | `_GfxDrawIndexed` `0x15eaa77` |
| Post-effects `GfxDraw` | `0xed7ba4` | `e8e02b7100` | `_GfxDraw` `0x15ea789` |
| Sky `GfxDraw` (not patched) | `0x10d25be` | `e8c6815100` | `_GfxDraw` `0x15ea789` |

Sky stays disabled in the pinned settings ([`analysis/profiling.md`](../../profiling.md)). The later post-effects sites `0xed7c71` and `0xed7dc8` were not armed. The mesh dependency map allows up to six textures; that ceiling is not a concrete miss for the first draw, so both eligible sites were armed, mesh first.

## Capture

[`capture.m`](capture.m) wraps only those two verified Gfx calls, calls the original once, and returns that value. It is not linked into [`benchmark/eu4_b2_draw.mm`](../../../benchmark/eu4_b2_draw.mm). `glDrawArrays` and `glDrawElements` are recorded only while the wrapper is inside that original call. The replay uses a new framebuffer cleared to transparent black and depth 1, then restores the GL bindings it changed. A reference that matches the clear-only image is rejected. Stencil test enabled is rejected. Depth bytes from `GetDefaultDepthStencil` were not available; `depth_evidence` would be `gl`, and the B1 factory is used only when that GL state matches the B1 default. The factory bytes are generated in the Metal draw, not stored as a captured engine image.

The synthetic GL smoke test passed before the session: the original ran twice because of one re-entry, the outer return value stayed 7, the viewport and program were restored, and the reference differed from the clear.

## Session

`python3 benchmark/eu4_b2_capture.py` launched EU4 once for 300 seconds. `fixtures/b2/rejects.log` contains `mesh callsite rejected`. That build returned before installing the post-effects site. No `fixture.json` was written. A second launch was not taken.

The page protect used by that session omitted `VM_PROT_COPY`. [`capture.m`](capture.m) now requests `VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY` and still installs the post-effects site when the mesh site fails. That change was not run against EU4.

## What remains

`_GfxInit`, resource creation, and a live `_Gfx*` consumer are still open. B2 does not replace `_GfxInit`. Public CI compiles the Metal host and skips the pixel comparison when `fixtures/b2/fixture.json` is absent. Acceptance would be one local host run against a full fixture, with the reference hash recorded here.
