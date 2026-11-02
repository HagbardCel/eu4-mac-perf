# PR5 vertex buffers

Pinned Mach-O SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. RVAs are from image base `0x100000000`. Bodies are the sealed `gfx-batch-00044.zip` `static.jsonl` records.

## ABI

`_GfxCreateVertexBuffer` (`0x15ebcb3`) starts `55 48 89 e5 41 57`. `rdi` is unread. The particle call at `0x14d44df` passes null `rsi`, stride `rcx = 1`, usage `r8 = 1`, and a non-zero `rdx` count. `rdx` is stored at header `+4`, `rcx` at header `+0`, and the GL name at `+8` stays 0. The byte length is the `int32` product. A negative factor or a product above `0x7fffffff` stops. Null data is not read. Logical length 0 still allocates a 1-byte shared buffer; copies use the logical length.

The fault at `0x15ebd02` is the return from `ff 10` at `0x15ebd00`, `call qword ptr [rax]`, after `lea rax, [___glewGenBuffersARB]` (`0x2253630`). The replacement does not call it.

`_GfxDestroyVertexBuffer` (`0x15ebd4c`) starts `48 85 ff 74 2f`, not a `push %rbp` prologue. Null returns without freeing or changing `nGLVBCount`. The counter is the `int32` at `0x223857c`. It increments only after the entry is registered and decrements only when that header is freed.

`_GfxUpdateVertexBuffer` (`0x15ebd81`) uses the same 6-byte prologue as create. `rdi` is the handle and the master is `*rdi`. Lookup is the header pointer, not the zero GL name. `r9 == 0` replaces the store, including a null pointer, and does not write the header. A subrange rejects a null pointer when the size is non-zero, and accepts a copy only when `offset <= length` and `size <= length - offset`. A successful update stores `1` at master `+0x170` and does not call `___glewDisableVertexAttribArrayARB`.

Half-open diagnostic ranges are create `[0x15ebcb3, 0x15ebd4c)`, destroy `[0x15ebd4c, 0x15ebd81)`, update `[0x15ebd81, 0x15ebe5f)`.

## Ownership

The table lives for the process. Each entry strongly owns its `MTLBuffer` and stores the backend pointer without retaining it. Shutdown nils buffers for that backend and leaves the header records. Destroy after that frees the header once. An update whose buffer is already nil stops. A new buffer is allocated before it replaces the old one. Stops record the return address captured at the replacement entry, then terminate. Registry calls, including shutdown release, must be on the thread bound by successful `_GfxInit`.

## Offline result

`python3 tests/test_gfx_buffers.py`, `python3 tests/test_gfx_init.py`, and `python3 tests/test_metal_boot.py` passed. They cover the header and byte contracts, zero-length and null data, growth, insert and allocation failure, destroy after shutdown, the eleven-entry transaction, rollback after ten writes, and rejection of a destroy prefix other than `48 85 ff 74 2f`.

## Live result

No `metal_init` session has been run for this replacement. A later stop outside the create body is not, by itself, an observed return. The note for that session has to say which stack or call sequence shows the replacement returned, and has to mark the claim indirect when the return itself was not observed. Launcher `post_init_failure` can still accompany a passed PR5 gate. The historical PC-zero return at `0x15ebd02` fails the gate.
