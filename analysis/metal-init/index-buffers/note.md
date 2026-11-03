# Index-buffer contract

Authority is the instruction stream in sealed `gfx-batch-00044.zip` for the pinned GOG 1.37.5 executable `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. The dossiers under `analysis/reconstruction/gfx-semantic/v1/buffers/` are hypotheses. This note is the replacement contract. Do not copy the vertex-buffer header, stride, or `nGLVBCount` behavior.

The callsite RVA `0x14bfa60` is not in that batch. The recorded `metal_init` crash is the return into `CreateIndexBuffer` from `CPdxMeshType::InitForDevice`. The replacement returns the header in `RAX`. No caller body was recovered, so nothing beyond that return and the rules below is claimed.

## Entries

Half-open bodies are the census bounds. Each prefix is a complete-instruction run.

| Name | Entry | End | Prefix | Bytes |
|---|---|---|---|---|
| `_GfxCreateIndexBuffer` | `0x15ec154` | `0x15ec1e6` | `55 48 89 e5 41 57` | 6 |
| `_GfxDestroyIndexBuffer` | `0x15ec1e6` | `0x15ec23c` | `48 85 ff 74 50` | 5 |
| `_GfxUpdateIndexBuffer` | `0x15ec23c` | `0x15ec28e` | `55 48 89 e5 41 56` | 6 |
| `_GfxSetIndexBuffer` | `0x15ec28e` | `0x15ec2bc` | `48 85 f6 74 28` | 5 |

Create is `push rbp; mov rbp, rsp; push r15`. Update is `push rbp; mov rbp, rsp; push r14`. Both already match the patcher's `55 48` prologue. Destroy is `test rdi, rdi; jz +0x50`. Set is `test rsi, rsi; jz +0x28`. Those two exact sequences are accepted beside the vertex destroy prefix `48 85 ff 74 2f`. A different `jz` displacement stays rejected.

Append these lines to `benchmark/eu4_gfx_entries.def`. Leave every existing line unchanged.

```
GFX_ENTRY(_GfxCreateIndexBuffer, 0x15ec154, 0x15ec1e6, eu4_gfx_create_index_buffer, "\x55\x48\x89\xe5\x41\x57")
GFX_ENTRY(_GfxDestroyIndexBuffer, 0x15ec1e6, 0x15ec23c, eu4_gfx_destroy_index_buffer, "\x48\x85\xff\x74\x50")
GFX_ENTRY(_GfxUpdateIndexBuffer, 0x15ec23c, 0x15ec28e, eu4_gfx_update_index_buffer, "\x55\x48\x89\xe5\x41\x56")
GFX_ENTRY(_GfxSetIndexBuffer, 0x15ec28e, 0x15ec2bc, eu4_gfx_set_index_buffer, "\x48\x85\xf6\x74\x28")
```

## Registers

- Create: `RDI` unused, `RSI` index bytes, `EDX` count. Return `RAX` = header.
- Destroy: `RDI` header. Null jumps to the final `ret`.
- Update: `RDI` unused, `RSI` header, `RDX` bytes, `ECX` count.
- Set: `RDI` is the address of the master pointer (`master = *RDI`), `RSI` header. Null `RSI` jumps to the final `ret`.

## Header, bytes, and globals

The header is 8 bytes from `operator new(8)`, or a recycled header. `+0` is the `uint32` count. `+4` is the GL element-array name. Create always calls `glGenBuffers` into `+4`, so a live original header has a nonzero name. Update reads `+4` and returns without an upload when it is zero. The replacement writes `1` there after a successful create. That dword is not the `MTLBuffer` identity. The registry key is the header pointer.

Index bytes are 16-bit. Create's length is the zero-extended count doubled (`mov esi, count; add rsi, rsi`). Update's length is the sign-extended effective count doubled. The original create does not reject a negative `EDX` and does not skip a null pointer. The replacement rejects `count < 0` and a product above `0x7fffffff` before allocation, with no counter change and no header retained. Zero is legal: logical length 0, and the data pointer is not read. A positive length with a null pointer allocates that many bytes and does not read the pointer. Logical length 0 uses the existing one-byte shared backing store.

`nGLIBCount` is the dword at RVA `0x2238584`. The vertex counter at `0x223857c` is a different global. The original create increments `nGLIBCount` before allocation and has no failure path. The replacement increments it only after the header and `MTLBuffer` are registered, and a failure rolls the new entry back completely.

The cache top is the dword at `0x2238588`. The pointer array is `0x2238590`, 128 slots. Create pops when the top is nonzero. Destroy, after releasing the GL name, pushes the header when the top is `<= 0x7f` and otherwise `operator delete`s it, then decrements `nGLIBCount`. Null destroy does not change the counter. Destroy does not write master `+0x68` and does not unbind. Use these RVAs through `eu4_gfx_image()`. A header sitting only in the cache is not a live registry entry.

## Replacement behavior

Keep a second registry beside `EU4VertexBuffer`, keyed by the header, holding the `MTLBuffer`, the logical byte length, the index count, and the non-owning backend token. Reuse `eu4_gfx_require_thread`, shared-storage allocation, and `eu4_gfx_release_vertex_buffers`. That release nils index buffers for the same backend. It does not free headers, change `nGLIBCount`, or change the cache. It drops the current binding when that binding's buffer is released.

Create copies the supplied bytes with no conversion. The same `eu4_gfx_buffer_test_fail_metal`, `_throw`, `_entry`, `_insert`, and `_header` hooks apply. On failure, stop with `_GfxCreateIndexBuffer` and `metal`, `header`, `entry`, or `insert`, restore a cache pop, and leave the counter unchanged. There is no GL-name identity.

Update replaces the whole allocation. A zero `+4` returns without changing bytes or the stored count. A requested count `< 1` uses the stored count and does not write `+0`. A positive count changes the GPU length and also leaves `+0` unchanged. Allocate the new buffer before releasing the old one. A failed allocation leaves the old buffer and stops with `_GfxUpdateIndexBuffer`. An unknown header stops `missing`. A registered buffer whose `MTLBuffer` was released stops `released`.

Destroy of null returns. Destroy of an unregistered pointer stops `_GfxDestroyIndexBuffer` / `missing` and does not decrement the counter. The original would decrement again; this registry must not. A live destroy releases the GPU resource, recycles or deletes the header as the cache rule says, decrements `nGLIBCount` once, and clears the current binding when it was this header.

Set of null returns without writing `+0x68`. Set of an unregistered header stops `_GfxSetIndexBuffer` / `missing` and does not write. Set of a registered header stores that `MTLBuffer` as the current index resource and writes the stored count to `master + 0x68`. It creates no command buffer. This does not show that an indexed draw consumes the resource.

An off-thread call stops `thread` for that symbol. A create with no device, backend, or image stops `no_backend`.

Declare these as `extern "C"`:

```c
void *eu4_gfx_create_index_buffer(void *ignored, const void *data, int32_t count);
void eu4_gfx_update_index_buffer(void *ignored, void *header, const void *data, int32_t count);
void eu4_gfx_destroy_index_buffer(void *header);
void eu4_gfx_set_index_buffer(void *handle, void *header);
int eu4_gfx_index_records(void);
int eu4_gfx_index_gpus(void);
size_t eu4_gfx_index_buffer_length(void *header);
int eu4_gfx_index_buffer_read(void *header, void *dest, size_t n);
int eu4_gfx_index_buffer_shared(void *header);
void *eu4_gfx_current_index_header(void);
```

`eu4_gfx_index_buffer_length` returns `(size_t)-1` for an unknown header and the logical length for a retained header whose `MTLBuffer` was released. Read returns 0 only when the logical length is `n` and the buffer is live. Shared returns 1 only for `MTLStorageModeShared`.

On the first successful create, before returning, call `eu4_metal_events_index_create(caller, (uintptr_t)buffer, length, count)` once. `caller` is `__builtin_return_address(0)`. Do not call it after a failure, and do not exit because of it.
