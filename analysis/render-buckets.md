# GOG EU IV draw-bucket path, static inspection

Inspected the pinned GOG v1.37.5 x86-64 executable (SHA-256
`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`)
with `llvm-objdump --disassemble-symbols`. This is a control-flow and data-flow
map, not a dynamic attribution of the ~6,000 paused draws per frame.

## Call path and draw unit

`CGraphics::RenderBuckets` at `eu4+0x149cedc` obtains the immediate graphics
context from the `CGraphics+0x80` field and calls
`CPdxMeshObject::RenderBuckets` at `eu4+0x14c7da4`, then
`CArrowObject::RenderBuckets`. Optional normals, bounding volumes, skeleton,
and names are gated by debug flags. Consequently, `CGraphics::RenderBuckets`
is a mesh/arrow dispatch point, not a complete renderer or a reliable place to
count terrain and UI draws.

The mesh routine selects `_FlushData` or `_TransparentFlushData` by its final
boolean argument. Each render-layer entry is 24 bytes; the layer index is the
routine's integer argument. It iterates a nested array of 80-byte records,
then an array of 0xe8-byte subrecords. A subrecord passes through effect,
texture, vertex/index buffer, object-constant and draw setup. At
`eu4+0x14c8404`, the innermost loop makes **one `GfxDrawIndexed` call per
subrecord**. That is the concrete draw unit for this path.

```text
CGraphics::RenderBuckets
  ├─ CPdxMeshObject::RenderBuckets
  │    └─ selected layer / opaque-or-transparent flush data
  │         └─ 80-byte record
  │              └─ 0xe8-byte subrecord
  │                   ├─ effect, texture, buffer and constants setup
  │                   └─ GfxDrawIndexed → glDrawElements[BaseVertex]
  └─ CArrowObject::RenderBuckets
```

## State applied around each subrecord

The mesh routine chooses a graphics effect via `CEffectHandler::GetGfxEffect`
at `eu4+0x14c81f3`. When that pointer differs from the previous effect, it
sets rasterizer, depth/stencil, blend and shader state (`+0x14c8204` through
`+0x14c8247`). It resolves three texture IDs through `CTextureHandler` and
calls `GfxSetTextures` (`+0x14c8274` through `+0x14c82dd`). It binds vertex
and index buffers at `+0x14c832a` and `+0x14c833b`. A six-slot texture loop
conditionally changes additional texture bindings (`+0x14c834e` through
`+0x14c83b1`). It then updates and binds object constants immediately before
the draw (`+0x14c83b3` through `+0x14c8404`). Animated records can also
update a bone constant buffer earlier in the loop.

`GfxSetIndexBuffer` at `eu4+0x15ec28e` binds an element-array buffer and copies
its first 32-bit field to graphics-context offset `+0x68`. The mesh call passes
zero for the draw count, so `GfxDrawIndexed` at `eu4+0x15eaa77` uses that
stored count. It uploads pending `glUniform4fvARB` constants and vertex
attributes as needed, then issues `glDrawElements` or
`glDrawElementsBaseVertex`. The observed index type is
`GL_UNSIGNED_SHORT`; the index offset passed to GL is zero. Whether two
subrecords can share one physical index buffer remains unproven.

## Implication for batching

The effect pointer is cached across adjacent subrecords, which saves some
shader/render-state changes, but each subrecord still has its own object
constants and draw. Replacing adjacent draws with one call is only safe if
their geometry can be combined **and** their texture, buffer, effect,
transform, object constants, depth/order, and transparency behavior are
equivalent. The current disassembly does not establish that. In particular,
the per-subrecord object-constant update is an obvious batching barrier;
transparent ordering is another. The earlier 25.8% adjacent tracked-state
match is therefore only a screening bound, not a safe merge rate.

The next offline target is the construction of `_FlushData` and
`_TransparentFlushData`: find who appends the 80-byte and 0xe8-byte records,
and which fields encode geometry ranges and material identity. Dynamic draw
category counts (terrain, UI, mesh objects, arrows) cannot be inferred from
this function alone. This phase adds no new game trace or launch for them.
