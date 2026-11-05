# Buffers

## API

`MTLBuffer` identity, storage mode, CPU access, and update lifetime. The OpenGL operations are `glBufferData` and `glBufferSubData`.

## Sources

- `Metal.framework/Headers/MTLBuffer.h`
- `Metal.framework/Headers/MTLResource.h`
- [MTLBuffer](https://developer.apple.com/documentation/metal/mtlbuffer)
- [`eu4_gfx_buffers.h`](../../benchmark/eu4_gfx_buffers.h)
- [Index buffers](../metal-init/index-buffers/note.md)
- [Vertex update](../automation/pilot-gfx-update-vertex-buffer-multiple/note.md)
- [Vertex selection](../automation/pilot-gfx-set-vertex-buffers/note.md)

## Provenance

Reviewed 2026-11-05 against macOS SDK 27.0. `MTLBuffer.h` and `MTLResource.h` were read. The `MTLBuffer` page was opened the same day. Synchronization and `didModifyRange:` are from the headers. Device affinity and the untyped length are from that page.

## Metal contract

[Apple docs] A buffer is usable only with the device that created it. [Apple docs] Metal records the length and not the layout of the bytes.

[SDK] `MTLResourceStorageModeShared` selects shared storage. [SDK] `MTLBuffer` says CPU and GPU access is not synchronized, unlike OpenGL. The storage-mode comment that names command-buffer coherency is written for a texture resource, so it is not used here as a buffer rule. [SDK] If the CPU and the GPU both write a buffer with no synchronizing action between those writes, the contents are undefined, including when the ranges do not overlap.

[SDK] `contents` returns the shared copy. [SDK] `didModifyRange:` is required only for `MTLResourceStorageModeManaged`, and it is not valid on other modes. [SDK] In SDK 27.0 that method is deprecated: managed storage has no effect on Apple silicon, and the replacement named in the header is shared storage.

## OpenGL differences

[Project-derived] `glBufferData` replaces the store behind a buffer name. `glBufferSubData` writes a range of the existing store. The GL name in an engine header is not the `MTLBuffer`. The index header's name dword is not the Metal object; the registry key is the header pointer. See [index buffers](../metal-init/index-buffers/note.md).

## Backend implications

[Project-derived] Vertex and index buffers are created with `MTLResourceStorageModeShared`. An update writes the existing buffer's `contents` and does not allocate a replacement. A failed update leaves that buffer unchanged. [`eu4_gfx_vertex_slot_buffer`](../../benchmark/eu4_gfx_buffers.h) returns the registry's current buffer and does not retain a copy. Release nils buffers this backend created and does not free engine headers.

[Project-derived] A logical length of 0 uses a 1-byte shared allocation. That is a local shortcut in `benchmark/eu4_gfx_buffers.mm`, not an Apple rule.

[Unresolved] The headers do not say which command-buffer boundary makes a shared-mode CPU write visible to a GPU read that was already encoded. This backend does not call `didModifyRange:`.

## Validation

[Project-derived] [`tests/test_gfx_buffers.py`](../../tests/test_gfx_buffers.py) covers create, in-place update, and the shared-mode check. The multiple-range update is recorded in [the vertex-update note](../automation/pilot-gfx-update-vertex-buffer-multiple/note.md).
