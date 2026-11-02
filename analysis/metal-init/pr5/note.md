# PR5 vertex buffers

Pinned Mach-O SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. RVAs are from image base `0x100000000`. Bodies are the sealed `gfx-batch-00044.zip` `static.jsonl` records.

## ABI

`_GfxCreateVertexBuffer` (`0x15ebcb3`) starts `55 48 89 e5 41 57`. `rdi` is unread. The particle call at `0x14d44df` passes null `rsi`, stride `rcx = 1`, usage `r8 = 1`, and a non-zero `rdx` count. `rdx` is stored at header `+4`, `rcx` at header `+0`, and the GL name at `+8` stays 0. The byte length is the `int32` product. A negative factor or a product above `0x7fffffff` stops. Null data is not read. Logical length 0 still allocates a 1-byte shared buffer; copies use the logical length.

The fault at `0x15ebd02` is the return from `ff 10` at `0x15ebd00`, `call qword ptr [rax]`, after `lea rax, [___glewGenBuffersARB]` (`0x2253630`). The replacement does not call it.

`_GfxDestroyVertexBuffer` (`0x15ebd4c`) starts `48 85 ff 74 2f`, not a `push %rbp` prologue. Null returns without freeing or changing `nGLVBCount`. The counter is the `int32` at `0x223857c`. It increments only after the entry is registered and decrements only when that header is freed.

`_GfxUpdateVertexBuffer` (`0x15ebd81`) uses the same 6-byte prologue as create. `rdi` is the handle and the master is `*rdi`. Lookup is the header pointer, not the zero GL name. `r9 == 0` replaces the store, including a null pointer, and does not write the header. The subdata offset is `r8d`: the body copies `r8d` to `r12d` and then zero-extends it with `mov esi, r12d`. A subrange rejects a null pointer when the size is non-zero, and accepts a copy only when `offset <= length` and `size <= length - offset`. A successful update stores `1` at master `+0x170` and does not call `___glewDisableVertexAttribArrayARB`.

A buffer stop uses `eu4_gfx_phase()`: 0 is `before_init`, 1 is `during_init`, and 2 is `after_init`. Create before a successful init is `before_init`.

Half-open diagnostic ranges are create `[0x15ebcb3, 0x15ebd4c)`, destroy `[0x15ebd4c, 0x15ebd81)`, update `[0x15ebd81, 0x15ebe5f)`.

## Ownership

The table lives for the process. Each entry strongly owns its `MTLBuffer` and stores the backend pointer without retaining it. Shutdown nils buffers for that backend and leaves the header records. Destroy after that frees the header once. An update whose buffer is already nil stops. A new buffer is allocated before it replaces the old one. Stops record the return address captured at the replacement entry, then terminate. Registry calls, including shutdown release, must be on the thread bound by successful `_GfxInit`.

## Offline result

`python3 tests/test_gfx_buffers.py`, `python3 tests/test_gfx_init.py`, and `python3 tests/test_metal_boot.py` passed. They cover the header and byte contracts, zero-length and null data, growth, insert and allocation failure, an Objective-C exception during Metal allocation and during entry construction, the `before_init` phase when no backend exists, the first `create.json` receipt, destroy after shutdown, the eleven-entry transaction, rollback after ten writes, and rejection of a destroy prefix other than `48 85 ff 74 2f`.

## Live result

[results/20261102T072350Z-metal-init](../../../results/20261102T072350Z-metal-init) is one launcher session of pid 5979. `init.json` records success 1 on thread 1533453. `stop.json` is `glViewport` at RVA `0x149b21f`, `after_init`, reason `gl`, same thread and the pinned image. The process exited 2. The launcher did not signal it. `metadata.json` status is `complete`. That session shows `OnResetDevice` returned. It does not record whether the particle count was non-zero, so it does not by itself show that create ran.

[results/20261102T100321Z-metal-init](../../../results/20261102T100321Z-metal-init) is pid 14511. `create.json` has `success` 1, symbol `_GfxCreateVertexBuffer`, the pinned image, pid 14511, and thread 1718011, the same thread as `init.json`. The RVA is `0x14d44e4`, the instruction after the particle call at `0x14d44df`. The receipt is written before the replacement returns, so it shows that creation path was reached. It does not, by itself, show that the caller resumed. The same process then stopped on `glViewport` at `0x149b21f`, after `OnResetDevice`, and exited 2 without a launcher signal. That later stop is what shows the particle call completed and execution continued. The pc-0 fault at `0x15ebd02` did not recur. The next dependency is `_GfxSetViewPort`.
