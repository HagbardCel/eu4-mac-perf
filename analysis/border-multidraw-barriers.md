# Border multidraw barriers and temporal side effects

## Draw invariants (must be constant across one multidraw)

From paused Venice intrusive screen + static path audit:

| Invariant | Border paused fixture |
|-----------|------------------------|
| Primitive mode | `GL_TRIANGLES` (via GfxDrawIndexed) |
| Index type | `GL_UNSIGNED_SHORT` |
| Element array / IBO | Single IBO binding (trace); per-iteration `GfxSetIndexBuffer` with handles from `0x60(%r12)` array |
| Shader program | One program (screening) |
| Textures | Four texture signatures (screening) |
| Uniforms | ~500 uniform signatures **across** border pass; **unchanged within same-state runs** |
| Blend / depth / raster | Stable within screened adjacencies |
| Index offset `indices[i]` | **0** (byte offset into bound IBO) |
| Varying per subdraw | `count[i]` (3× uint16 at +0x06); `indices[i]=0`. Record `+0x04` is **not** GL basevertex on mode-0 ([addendum](border-mode0-basevertex-evidence-addendum.md)) |

## Inter-draw side effects (temporal — Gate 3a)

| Segment | Risk | Assessment |
|---------|------|------------|
| `GfxSetVertexBuffers` | VBO bind | Mode-0 path: only when `+0x18` table index changes (`cmp` at `0x1010cc292`) |
| `GfxSetIndexBuffer` | IBO bind | Every mode-0 iteration; same handle array slot when IBO unchanged — **idempotent** |
| Color / `GfxUpdateConstantBuffer` | Uniform block | Skipped on **eligible** path: non-special outer batch, color match via `je 0x1010cc26b` @ `0x1010cc1d5` (not `jmp 0x1010cc22d` helper prelude) |
| Outer batch precolor `{4,5,7}` | Per-record helpers | Mask `0xB0` @ `0x1010cbe79` — classifier `OTHER_SIDE_EFFECT` at hook |
| `+0x10` helper arg | Site 2 only | `movl 0x10(%r14,%rbx), %ecx` before draw @ site 2 — **not** on mode-0 bulk path |
| `GfxProfileEnd` | CPU bookkeeping | After outer loop only (`0x1010cc400`) — not per inner draw |

### `+0x10` helper path

Used only on **draw site 2** (`0x1010cc3b8`): full 32-bit field passed as `%ecx` to `GfxDrawIndexed`. Mode-0 multidraw candidate runs **do not** load this field. Site-2 batches require separate barrier proof — excluded from initial implementable ceiling.

### Gate 3a verdict (mode-0 homogeneous runs)

For records that take the **color fast path** and **mode 0** with stable `+0x18` VBO index:

- Skipped work is either **idempotent GL binds** or **draw submission**.
- **No draw-dependent helper** must remain interleaved between draws in this subset.

When color byte changes or VBO table index changes, batch must **end** before that record (fail-closed fallback).

## Hard stops (semantic ceiling reductions)

- Mode 1 / site 1 / site 2 paths in same frame (cannot merge across mode branch).
- Records that take slow color path (cannot skip per-record constant upload without hoisting).
- VBO table index change mid-run (barrier between batches).
