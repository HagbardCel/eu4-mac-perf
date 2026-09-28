# GOG EU IV mesh-bucket construction and draw barriers

Static inspection is pinned to x86-64 GOG v1.37.5, SHA-256
`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.
Addresses below are preferred-image addresses; subtract `0x100000000` for image offsets.

`CPdxMeshObject::AddToBucket` (`0x1014c7168`) first checks the object's active
flag, computes a camera-distance LOD from the mesh type's distance array, and
appends an 80-byte `SFlushData` entry to one or more per-layer arrays. The
opaque, transparent, and shadow arrays start at `0x102251930`, `0x1022519c0`,
and `0x102251a50`; each layer descriptor is 24 bytes. Object transparency
(`CPdxMeshObject+0x60`) and mesh-type flags at `+0x148/+0x149` select the
arrays. `CArray<SFlushData>::Append` (`0x1014c72b0`) copies exactly `0x50`
bytes and advances its end pointer by `0x50`.

| `SFlushData` offset | Established meaning |
|---|---|
| `+0x00` | selected integer LOD/index, used to choose mesh-type draw records |
| `+0x08` | `CPdxMeshObject*` |
| `+0x10..+0x4f` | 64-byte object transform copied from the mesh object |

`CPdxMeshObject::RenderBuckets` (`0x1014c7da4`) walks the chosen layer's
80-byte entries. For each entry, it selects a 24-byte mesh-type descriptor by
the stored LOD, then iterates its nested `0xe8`-byte draw records. The
inner-loop call at `0x1014c8404` issues one `GfxDrawIndexed` per eligible
subrecord. In the observed code, subrecord `+0x7d` participates in the
opaque/transparent test, `+0x30` is passed as the first vertex-buffer handle,
`+0x38` as the index-buffer handle, and `+0x40` as an optional second
vertex-buffer handle. The object and mesh type supply additional effect and
texture IDs. The complete `0xe8`-byte layout is **not** established; unknown
fields must not be treated as batch-compatible.

The renderer avoids some work when the adjacent subrecord pointer or effect
pointer repeats, but it still updates `_ObjectConstants` (`0x102251ae8`) at
`0x1014c83d0`, binds them at `0x1014c83f6`, and draws at `0x1014c8404`.
The constant payload incorporates the `SFlushData` transform and may include
object-specific data. `GfxDrawIndexed` uses the currently bound index
buffer's stored count when passed zero; this path passes zero for count and
index offset. Thus same geometry handles do not by themselves make adjacent
draws mergeable: differing constants, texture/effect, depth/blend behavior,
and transparent ordering remain barriers. It also means contiguous index
ranges cannot be inferred merely from adjacent subrecords.

The [direct-call inventory](draw-callers.json) has 48 `GfxDraw*` call/jump
sites. They include terrain, water, borders, trees, post-effects, text/UI,
mesh objects, particles, and debug paths. These are **possible sources**, not
draw proportions. The first passive trace measured the per-frame contribution
of each call site and found about 0.6% of draws outside this direct-call
inventory. Exact categories such as province fills or unit sprites remain
broad unless their call path is independently established.

The largest outside-inventory return address, image offset `0x11cfddf`, is
immediately after `CTradeRouteObject::RenderBuckets` calls
`CPrimitivesContainer::Render`. It accounts for 928 of 186,179 captured draws.
It is kept in the trace's `unknown` category because the exact inner GL draw
path and state are not fully mapped; it does not affect the choice of border
versus mesh work.

## Border draw path found after the first trace window

`CPdxMapBorderLayer::DrawBorders` has three direct `GfxDrawIndexed` call sites
at `0x1010cc2e8`, `0x1010cc352`, and `0x1010cc3b8`. Its inner record stride is
28 bytes. The observed call paths pass a draw count derived from record `+0x06`,
an integer base vertex from `+0x04`, and either zero or the integer at `+0x10`
as the helper's fourth argument. The function selects a vertex buffer, selects
an index buffer on one path, then calls the draw helper. `GfxDrawIndexed`
chooses `GL_UNSIGNED_SHORT`, index offset zero, and tail-jumps to
`glDrawElementsBaseVertex` when the base vertex is nonzero. This tail jump
means the GL call's immediate return address is already the engine call site;
an extra stack-frame walk is wrong for attribution.

The captured complete frame has 2,285 border draws, all using one index buffer
and index offset zero, but 2,285 distinct base vertices. It uses one program,
four tracked texture signatures, and 500 tracked uniform signatures. The
different base vertices rule out a simple contiguous-index-range merge, while
`glMultiDrawElementsBaseVertex` can express differing base vertices in one API
call. That API is declared by the installed macOS OpenGL SDK. This only
establishes a candidate shape: exact ordering, intervening state, constant
updates, and driver behavior must be checked in a prototype.
