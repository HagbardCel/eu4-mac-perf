# `_FlushData` / `_TransparentFlushData` static RE (in progress)

Extends [render-buckets.md](render-buckets.md). Goal: explain how 80-byte layer records and 0xe8-byte subrecords are populated before `CPdxMeshObject::RenderBuckets` issues one `GfxDrawIndexed` per subrecord.

## Known from `RenderBuckets` disassembly

- Layer table: 24-byte entries; boolean selects opaque `_FlushData` vs `_TransparentFlushData`.
- Inner loop: 80-byte records → nested 0xe8-byte subrecords.
- Per subrecord: effect pointer cache, `GfxSetTextures`, buffer binds, six-slot texture loop, object constants, then `GfxDrawIndexed` at `eu4+0x14c8404`.

## Open questions (offline)

1. Which functions append to `_FlushData` / `_TransparentFlushData` for the paused Venice political map?
2. Which 0xe8 fields encode geometry range, material identity, and batch boundaries?
3. Can adjacent subrecords share effect/textures/buffers such that **one** submission could replace two without visual change?

## Method

- `llvm-objdump --disassemble-symbols` on symbols referencing flush data containers.
- Cross-reference call graph in [frame-model-static.json](frame-model-static.json) for `CPdxMeshObject` and map render paths.

No new EU IV capture required for this document phase.
