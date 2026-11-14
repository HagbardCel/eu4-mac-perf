# M6 post-merge correctness hardening

Follow-up to merged PR #189. Scope: make the indexed-draw, capture-range,
colour-mask, topology, unsupported-state and render-target-shadow contracts
faithful to the original EU4 OpenGL implementation, with regressions that fail
on the merged implementation. No renderer redesign and no new frame evidence
were introduced.

## Ghidra contract verification

| Function | RVA | Verified original behavior |
| --- | --- | --- |
| `_GfxDraw` | `0x15ea789` | `rdi`=context; `esi`=count (if `<1`, `master+0x64`); `edx`=first; mode from `master+8`; tail `glDrawArrays(mode, first, count)`. |
| `_GfxDrawIndexed` | `0x15eaa77` | `rsi`=count (if `<1`, `master+0x68`); `rcx` is a **signed** base vertex. `base==0` calls `glDrawElements(mode, count, 0x1403, 0)`; otherwise `glDrawElementsBaseVertex(mode, count, 0x1403, 0, base)`. `0x1403` is `GL_UNSIGNED_SHORT`; the index pointer is 0. Effective vertex = `index + base`. |
| `_GfxSetPrimitiveType` | `0x15ea76f` | `GetOpenGLPrimitiveType(EGFXPrimitive)` (`0x15edff7`) maps the table at `0x1a79600`: `[0,1,3,4,5,0xa,0xb,0xc,0xd]` and stores the GL token at `master+8`. The engine table contains no `GL_LINE_LOOP` (mode 2). |
| `_GfxSetBlendState` | `0x15ea0bf` | The four colour-write enables are descriptor bytes `0x20..0x23` (R,G,B,A, 0/1); they are copied to `master+0x2c` and passed to `glColorMask` on change. Blend enable/factors/equations are at `master+0xc..0x24`. |
| `_GfxSetDepthStencilState` | `0x15ea261` | Depth enable `master+0x30`, write `+0x31`, compare `+0x34`. Stencil enable `+0x38`, reference `+0x3c`, function/mask `+0x40/+0x44`, ops `+0x48/+0x4c/+0x50`. |
| `_GfxSetRasterizerState` | `0x15ea35a` | Cull mode `master+0x54`, polygon mode `+0x58` (`0x1b02`=GL_FILL, `0x1b01`=GL_LINE), front face `+0x5c`, polygon-offset-fill enable `+0x60`, cull enable `+0x61`. |
| `_GfxResolveTexture` | `0x15ece63` | Returns when source==destination and destination `+0x1b==0`; binds read FBO `source+8`, draw FBO `destination+0xc`, `glBlitFramebuffer(0,0,dstW,dstH, 0,0,srcW,srcH, GL_COLOR_BUFFER_BIT, GL_NEAREST)`, then restores the draw FBO and viewport from `master+0x78`. |
| `_GfxSetIndexBuffer` | `0x15ec28e` | Binds `GL_ELEMENT_ARRAY_BUFFER` (`0x8893`) to the GL name at `header+4` and stores the count at `master+0x68`. The engine indices are 16-bit. |
| `_GfxSetVertexBuffers` | `0x15ebf41` | Stores each slot header at `master+0x130+slot*8`, its binding offset at `master+0x150+slot*4`, and its stride at `master+0x160+slot*4`. Slot 0 also writes `master+0x64`/`master+0x6c`. The slot offset is a buffer origin, not a vertex-attribute offset. |
| `SShaderOpenGL::EnableVertexAttribArrays` | `0x15ea970` | Binds `GL_ARRAY_BUFFER` per slot and calls `glVertexAttribPointer(location, size, type, normalized, slot_stride, element_offset + slot_offset)`; divisor comes from element byte `+0x20` and normalized from `+0x21`. Element stride is `0x28`. |

## Original defects and resolutions

| Area | Merged #189 behavior | Correction |
| --- | --- | --- |
| Indexed base vertex | Rewrote indices as `index - base`, clamped negatives to 0, installed a UInt32 buffer. Contradicts `glDrawElementsBaseVertex` and changes sparse-index meaning. | Live generic now preserves the 16-bit indices and passes the signed `baseVertex` to Metal's native indexed overload; validates the exact `bound_offset + index*stride + format_width` bytes for every attribute (including converted integer formats and per-instance divisors), and refuses negatives/out-of-range. |
| Replay index origin | Applied the live subtraction logic to captured buffers with no notion of the captured slice origin. | Capture records the true effective range and `vertex_origin`; replay rebases `index + base - origin` into the captured vertex blob, keeps UInt16, validates against the captured vertex count, and refuses invalid ranges. |
| Indexed capture spans | Assumed `count` consecutive vertices (`base..base+count-1`), over/under-capturing sparse indices and truncating attributes. | Capture reads the actual 16-bit indices, computes min/max effective vertices with the signed base, uses that range for per-vertex attributes, computes instance-rate ranges from instance count/divisor, records `vertex_origin`/`range_valid`/`range_truncated`/`read_error`, and fails the event on truncated/error data. |
| Colour write mask | Generic pipeline ignored `master+0x2c`. | `eu4_gfx_color_write_mask` maps GL R,G,B,A to Metal's BGRA-ordered write-mask bits; the mask is applied to the pipeline and included in the cache key. Disabled channels preserve the destination. |
| Primitive topology | Mode 2 (line loop) mapped to line strip; mode 3 (line strip) mapped to triangle strip; defaults silently became triangles. | Shared `eu4_gfx_metal_topology`: 0 point, 1 line, 3 line strip, 4 triangle, 5 triangle strip; mode 2 and unknown modes are refused with a diagnostic. |
| Unsupported state | Stencil, non-fill polygon mode and polygon offset were silently ignored. | Generic live encoder and replay refuse stencil-enabled, non-fill polygon mode and polygon offset; disabled/`GL_FILL` states remain accepted without inspecting unused bytes. |
| Capture state preservation | Used the invalid `GL_VERTEX_ATTRIB_ARRAY_POINTER` query and left `GL_ARRAY_BUFFER`/`GL_ELEMENT_ARRAY_BUFFER` rebound. | Uses `glGetVertexAttribPointerv`; saves and restores both bindings on every path; keeps the verified engine attribute layout where available; propagates attribute/index read errors instead of zeroing them. |
| Shadow cache lifetime | Marked a shadow valid when the flip was encoded, even if the command was later abandoned; a failed conversion could fall back to the raw top-left texture; resolve/clear writes were not all invalidating. | Shadows have a pending-command state promoted only by command completion; abandoned commands leave them invalid and the next draw re-encodes. Conversion failure refuses the draw instead of binding the raw resource. Draw, clear, initialize and both resolve paths invalidate the relevant generation. |
| Shadow tests | Initial conversion and ordinary-texture preservation only. | Native lifecycle test covers initial conversion, repeated sampling, modification, abandoned-command recovery, conversion failure and ordinary-texture preservation. |

## Regression tests added/updated

- `tests/gfx_live_draw_host.mm --draw-semantics`: topology mapping, Metal
  colour-mask conversion, all required signed base/index cases (including
  `65535` and a negative effective index), and replay-index rebasing.
- `tests/gfx_live_draw_host.mm --indexed-base-vertex`: GPU draw with a vertex
  buffer whose vertices 4/5/6 are green and 0/1/2 red; base 4 reads green,
  base 0 reads red. This establishes the actually fetched vertices, not the
  rewritten index values.
- `tests/gfx_live_draw_host.mm --color-write-mask`: all channels, none,
  alpha-only, RGB-only and a partial mask; checks the destination pixels of
  disabled channels.
- `tests/gfx_live_draw_host.mm --sampled-rt-orientation`: initial conversion,
  repeated sampling, modification, abandoned-command release/recovery,
  a stale command that actually encodes a pending conversion before its
  generation is advanced, stale completion after a newer write, conversion
  failure, missing render-target storage, and ordinary-texture preservation.
  A test-only conversion counter proves the pending path was entered.
- `tests/gfx_live_draw_host.mm --production-bounds`: the production attribute
  bounds validator with tight 72/80-byte buffers, 71/79-byte rejects, nonzero
  slot/element offsets, instance-rate divisors and unsupported formats.
- `tests/gfx_live_draw_host.mm --production-pipeline`: the production pipeline
  cache with all/alpha colour masks, cache separation and rendered channel
  preservation.
- `--draw-semantics` also covers the shared byte-range helper (tight final
  vertex, overflow, GL type widths, interleaved offsets).
- `tests/test_m6_frame.py`: the synthetic triangle-strip fixture now uses the
  correct GL token 5 (mode 3 is a line strip).
- The full canonical replay is the capture-to-replay regression for the
  existing recorded frame; its captured indexed draws exercise the replay
  rebase path.

All new regressions fail on the merged implementation:
- `--draw-semantics` fails on the old topology map and the old index formula.
- `--indexed-base-vertex` fails with the old subtract/clamp rewrite (base 4
  reads red instead of green).
- `--color-write-mask` fails when the mask is ignored (all channels change).
- `--sampled-rt-orientation` fails on the abandoned/failed-generation cases
  and the old unconditional-valid cache.

## Verification results

| Check | Result |
| --- | --- |
| `tests.test_gfx_live_draw` | 29 passed (semantics, base-vertex GPU, colour-mask GPU, production bounds/pipeline, shadow-cache lifecycle) |
| `tests.test_m6_frame` | 29 passed |
| `tests.test_m6_live_accept` | 25 passed |
| `tests.test_gfx_texture`, `test_gfx_contracts`, `test_gfx_compare`, `test_gfx_shader`, `test_gfx_default_target`, `test_m4_accept`, `test_m5_accept`, `test_m5_replay_cache` | pass |
| Canonical 326-event replay | 326/326 encoded, zero failed, GPU completed, 0 differing pixels of 1,930,176 including alpha |
| Missing render-target storage | rejected with `render-target header has no backing texture`; no fallback binding |
| Existing three-start M6 acceptance | re-run against the committed artifacts; unchanged |
| Fresh live start | `.local/m6/live-hardening3/run-1`, identity `1794689243-84463`, library `00eb43…`: executed, encoded, GPU completed, presented, **0 refused draws**, 0 failed resolves; UI 0.882/0.946/0.835, map mean 72.247 / affinity 0.967, text upright 0.905/0.937 vs reflected 0.486/0.513; accepted. |

The first fresh run (`live-hardening/run-1`) exposed an over-strict indexed
range check at caller `0x10d2993`: the limit assumed whole strides instead of
the exact final attribute bytes. The check now uses the actual
`bound_offset + index*stride + format_width` against the bound buffer and
distinguishes per-instance divisors; the final run was clean. This is recorded
because it is exactly the kind of live-only contract the native tests cannot
fully exercise.

## Remaining limitations

- The repository has no GL-context capture host, so the capture-side sparse
  index / instance-divisor / truncation logic is covered by the shared range
  helpers and native tests, not by a live GL recorder fixture. A genuine
  capture-to-replay test with non-zero `vertex_origin` remains a follow-up; the
  canonical recorded frame predates this capture change and its indexed origins
  are zero, so it does not exercise a non-zero origin.
- Full stencil emulation and line polygon mode are not implemented; both are
  refused fail-closed. No current qualifying scene uses them.
- Polygon offset is refused. No current M6 draw enables it.
- The shadow conversion allocates one sampler per rebuild; the affected UI
  targets are 168x63 and no measurable regression was observed.
