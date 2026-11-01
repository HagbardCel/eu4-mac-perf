# B2 — one real EU4 draw on Metal

B2 is incomplete. The one authorized EU4 session did not install a callsite patch, so no original-input fixture was frozen and no EU4 pixels were compared.

## Callsites

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Offsets are image-relative (`callsite_offset` against `image_base` `0x100000000` in [`analysis/draw-callers.json`](../../draw-callers.json)). In this Mach-O, `__TEXT` has `vmaddr` `0x100000000` and `fileoff` 0, so these three RVAs are also file offsets. The capture still adds the RVA to the loaded image header.

| Site | RVA | Bytes | Decoded target |
| --- | --- | --- | --- |
| Mesh `GfxDrawIndexed` | `0x14c8404` | `e86e261200` | `_GfxDrawIndexed` `0x15eaa77` |
| Post-effects `GfxDraw` | `0xed7ba4` | `e8e02b7100` | `_GfxDraw` `0x15ea789` |
| Sky `GfxDraw` (not patched) | `0x10d25be` | `e8c6815100` | `_GfxDraw` `0x15ea789` |

Sky stays disabled in the pinned settings ([`analysis/profiling.md`](../../profiling.md)). The later post-effects sites `0xed7c71` and `0xed7dc8` were not armed. Only the mesh site is patched. Post-effects stays in the table above and is not installed.

## Capture

[`capture.m`](capture.m) patches mesh `0x14c8404` only. It is not linked into [`benchmark/eu4_b2_draw.mm`](../../../benchmark/eu4_b2_draw.mm). The wrapper calls the original through `%r10` and returns that value. The only draw that can write a fixture is one ordinary `glDrawElements` of triangles: one `uint16` or `uint32` index buffer whose `index_offset + count * index_size` fits the captured bytes, and one array buffer whose attributes keep their original locations and one shared stride. An active uniform, including a sampler, or a texture is a reject. `glDrawElementsBaseVertex` is not interposed, so a Gfx call that never reaches `glDrawElements` is a reject, as is a second draw. Also rejected, and not implemented: primitive restart when the context can report it, a depth range other than `0..1`, polygon offset fill, an index range past the buffer, a nonzero pixel-pack buffer (it is left bound), a nonzero viewport origin, stencil, MSAA, and sRGB. The depth clear forces the write mask on, then restores the captured mask. A `GL_PRIMITIVE_RESTART` query on this legacy profile raises `GL_INVALID_ENUM`, which cannot be put back, so the capture queries it only when `GL_VERSION` is 3 or newer. Depth, blend, cull, and scissor stay the fixed state the host already applies. Depth bytes from `GetDefaultDepthStencil` were not available; `depth_evidence` is `gl`, and the B1 factory is used only when that GL state matches the B1 default.

Replay creates its own VAO, binds the index buffer and the original attribute locations on it, draws, deletes it, and the saved VAO is bound again. On this driver a generic attribute above location 0 is fetched only while location 0's array is enabled, so a draw that does not use location 0 enables a dummy location 0 on that VAO for the draw. Read and draw framebuffer bindings are saved and restored separately, with the clear color, clear depth, renderbuffer binding, and the one texture unit used for the color attachment. Replay does not call `glGetError`. The reference clear is transparent black at depth 1. A reference that matches the clear is rejected.

[`analysis/mesh-draw-dependency-map.md`](../../mesh-draw-dependency-map.md) updates and binds `_ObjectConstants` on every subrecord and resolves textures in the draw loop. A shader at this callsite with zero uniforms and zero textures may not exist. This change does not add that support and does not search the effect data. Another session is not authorized.

The ABI check calls the wrapper directly. Six register arguments and one stack qword, including `%rcx`, are unchanged, the return value is the original's, the normal path calls the original once, and re-entry calls it again without a second capture. The GL smoke is a separate executable. After the success draw it checks a nondefault clear color and depth, a renderbuffer, a sentinel element binding, a texture on unit 2, and distinct read and draw framebuffer bindings. A pack buffer, two uniforms, a base-vertex draw, a nondefault depth range, polygon offset, and an index past the buffer write no fixture. Primitive restart cannot be enabled here, so that smoke case is skipped. One synthetic Metal draw, with an attribute location that is not the array order, matches the GL reference on every channel. `fixtures/b2/fixture.json` is absent. CI does not launch EU4 and does not claim an EU4 pixel match.

## Session

`python3 benchmark/eu4_b2_capture.py` launched EU4 once for 300 seconds. `fixtures/b2/rejects.log` contains `mesh callsite rejected`. That build returned before installing the post-effects site. No `fixture.json` was written. A second launch was not taken.

The page protect used by that session omitted `VM_PROT_COPY`. [`capture.m`](capture.m) now requests `VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY`. That change was not run against EU4.

## What remains

`_GfxInit`, resource creation, and a live `_Gfx*` consumer are still open. B2 does not replace `_GfxInit`. Public CI compiles the Metal host and skips the pixel comparison when `fixtures/b2/fixture.json` is absent. Acceptance would be one local host run against a full fixture, with the reference hash recorded here.
