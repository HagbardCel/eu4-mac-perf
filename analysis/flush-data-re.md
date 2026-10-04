# `_FlushData` / mesh draw subrecord static RE

Extends [render-buckets.md](render-buckets.md). Pinned GOG v1.37.5 x86-64.

## Structures (do not conflate)

```text
SFlushData (0x50) — CPdxMeshObject::AddToBucket / CArray<SFlushData>::Append
        ↓
mesh-type descriptor (24-byte layer entry selects opaque vs transparent flush array)
        ↓
mesh draw subrecord (0xe8) — nested in mesh-type data, not appended via SFlushData::Append
        ↓
GfxDrawIndexed per subrecord (landmark 0x14c8404)
```

Layouts: [sflushdata-0x50-layout.json](sflushdata-0x50-layout.json), [mesh-draw-subrecord-0xe8-layout.json](mesh-draw-subrecord-0xe8-layout.json).

## Reachability (static)

Map render path reaches `CPdxMeshObject::RenderBuckets` via `CGraphics::RenderBuckets` ([mesh-draw-dependency-map.md](mesh-draw-dependency-map.md)). Static RE does not prove every constructor ran in a particular Venice frame.

## Open follow-ups

1. Mesh-type **writers** for 0xe8 records (loaders/constructors off hot path).
2. Prove texture/object-constant dependency fields in 0xe8 layout (required before `texture_setup_elision_eligible` / `object_constants_elision_eligible`).
3. Dynamic trace only if static ROI gate fails and field proof requires runtime samples.

Hook ABI (13-byte patch @ `0x14c81e6`, frame offsets, resume addresses): [mesh-subrecord-hook-site.json](mesh-subrecord-hook-site.json).

## PR1 decision

See [gfx-subrecord-pr1-gonogo.md](gfx-subrecord-pr1-gonogo.md): **buffer-bind predicate only**; full setup elision and draw batch not established.
