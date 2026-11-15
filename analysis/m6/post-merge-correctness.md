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

## Vertex attribute capacity

The `0x15ea970`/`0x15ebf41` bodies do not impose a 16-attribute limit:
`SShaderOpenGL::EnableVertexAttribArrays` iterates the shader's full element
count (`SShaderOpenGL+0x18`, guarded at 64 by the generic encoder) across the
four vertex-buffer slots, using element location `+0xc`, offset `+0x18`,
divisor `+0x20` and the slot offset. The 16-entry array was a diagnostic cap,
not an engine contract. Correctness tracking now covers all 31 Metal attribute
locations (`0..30`); the shared capacity guard checks the attribute location
and the next vertex-buffer index (`8..30`), refusing with
`vertex attribute location exceeds Metal capacity` or
`vertex descriptor buffer capacity exceeded`. Conversion to FloatN updates the
tracked format on the attribute's own record, and no code mutates the previous
entry when tracking is full.

Regression coverage: 17 valid attributes accepted; the 17th out of bounds
refused; the 16th out of bounds with a valid 17th refused (proving no overwrite
of the 16th record); capacity guard accepts `next_buffer=25, location=30` and
refuses `31`/`location=31` with diagnostics.

## Capture range completeness

`capture_attributes` now selects ranges in the original GL order:
instance-rate attributes first (`0..(instance_count-1)/divisor`), then indexed
per-vertex attributes from the actual sparse index range, then nonindexed
per-vertex attributes as `first..first+count-1`. Nonindexed draws with a
positive divisor therefore capture element 0 regardless of `first`; indexed
instanced draws use the actual instance count. The speculative signed-32-bit
`base_vertex + count - 1` initialization was removed; indexed ranges come only
from the read index data. Nonindexed arithmetic is checked in 64 bits and
refused on overflow rather than clamped.

`GL_ACTIVE_ATTRIBUTES` is checked against `FTRACE_MAX_ATTRS`; overflow marks
the capture failed and publishes the frame with `complete=false` and reason
`attribute_capacity`. A local CGL query of the legacy Apple OpenGL environment
reports `GL_MAX_VERTEX_ATTRIBS=16`, so the condition is impossible there with
the current 16-entry representation; the guard remains for environments with a
larger limit.

Regressions cover nonindexed divisor 0/1/2 ranges, indexed `[0,4,7]` base 3
(3..10), five instances divisor 2 (0..2), `INT32_MAX` overflow, invalid first,
and attribute counts 16/17 against the recorder limit.

## Vertex-fetch semantics

`SShaderOpenGL::EnableVertexAttribArrays` (`0x15ea970`) configures
`glVertexAttribDivisorARB` from the shader element descriptor (`+0x20`), not
from the draw entry point. `_GfxDraw` (`0x15ea789`), `_GfxDrawIndexed` (`0x15eaa77`) and
`_GfxDrawIndexedInstanced` (`0x15eac9c`, third argument is instance count) do
not change that divisor. Corrections:

- Divisor `0` is `MTLVertexStepFunctionPerVertex`/rate 1; divisor `>0` is
  `MTLVertexStepFunctionPerInstance`/rate divisor, independent of
  `instance_count`. A non-instanced draw is one instance and therefore fetches
  element 0 of every per-instance attribute for all of its vertices.
- The live builder no longer takes an `instanced_draw` flag. Replay keeps the
  captured divisor in the attribute plan and pipeline descriptor; it validates
  per-instance captured buffers against `(instances-1)/divisor`.
- Declared-but-unbound and disabled generic attributes use
  `MTLVertexStepFunctionConstant` with rate 0 and a 16-byte single-value buffer.
  The GPU fetches element 0 for every vertex and instance. Disabled attributes
  preserve the captured `constant_value`; the zero/one fallback for
  declared-but-unbound attributes remains a documented limitation because the
  original GL generic attribute value is not in the trace.

Regression coverage: direct GPU one-instance divisor-1 (all vertices green,
not interpolated), three-instance divisor-2 (instances 0/1 green, instance 2
red), constant attribute across vertices/instances, tight one-element bounds;
synthetic replay one-instance divisor-1 plus disabled constant and
three-instance divisor-2 descriptor checks.

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
  slot/element offsets, instance-rate divisors, unsupported formats, 17 active
  attributes (including the 16th/17th ordering cases) and the descriptor
  capacity guard.
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
| `tests.test_gfx_live_draw` | 30 passed (semantics, base-vertex GPU, colour-mask GPU, vertex-fetch GPU, production bounds/pipeline, shadow-cache lifecycle) |
| `tests.test_m6_frame` | 30 passed |
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

## Follow-up status (2026-11-15)

The non-zero-`vertex_origin` follow-up is now covered by
`tests.test_m6_frame.SparseIndexReplayTests`, a capture-format/replay
regression. It builds a frame-trace document in the captured format (it does
not exercise the OpenGL capture writer, so it covers the capture-format fields
and replay-side rebasing, not the GL recorder) with 16-bit indices `[3, 5, 7]`,
signed base vertex `2` and captured `vertex_origin` `5`, replays the selected
draw through the production frame replay host with the authentic pre-image, and
checks the rendered pixels.

A second case omits `vertex_origin` and appends off-screen vertices so the
unrebased indices stay in range. The replay still completes, but it fetches the
wrong vertices and cannot reproduce the captured triangle, so a missing origin
cannot produce a falsely successful, correct draw. The historical
canonical-frame replay above is unchanged and remains the full-frame
qualification.
