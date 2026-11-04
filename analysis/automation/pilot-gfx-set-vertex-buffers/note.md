# `_GfxSetVertexBuffers` research record

Machine evidence for the original function, written before any replacement. Sources are `reverse_engineering.research` and `reverse_engineering.ghidra_query` against the qualified Ghidra tree `/Users/fabian/ghidra_12.2_PUBLIC`. No per-function dossier was used.

## Identity

`research resolve _GfxSetVertexBuffers` is exact and in the renderer snapshot. Entry `0x15ebf41`, 125 instructions, static and sealed decompile both `success`, batch `gfx-batch-00044`. The body ends at the `RET` `c3` at `0x15ec11b`, so the half-open range is `0x15ebf41`–`0x15ec11c`. The first six bytes are `55 48 89 e5 41 57`.

`ghidra_query function 0x15ebf41` on the qualified install also returned `decompile_status: success`, 125 of 125 instructions, and 2933 characters of pseudocode. That text agrees with the sealed decompilation below. It is Ghidra's recovery, not a separate proof.

There are 51 direct incoming calls. Nine outgoing edges are the logger and locale calls below. One callsite, `0x15ebf9d`, is an unresolved `CALLIND`.

## Calling convention and parameters

System V. The prologue saves `RBP`, `R15`, `R14`, `R13`, `R12`, and `RBX`, then `SUB RSP, 0x158`.

| Register | Saved at | Role from the instructions |
| --- | --- | --- |
| `RDI` | dereferenced at `0x15ebf67` | Pointer to the master pointer. `RAX = [RDI]`, then saved at `[RBP-0x40]`. |
| `ESI` | `[RBP-0x30]` at `0x15ebf64` | Count. Zero skips the per-slot loop (`JZ` at `0x15ec0c3` waits on the `TEST` at `0x15ebfb1`). |
| `RDX` | `[RBP-0x48]` at `0x15ebf60` | Header pointer array. `0x15ebfdd` loads `[R8 + R15*2]` while `R15` steps by 4, so each step is one pointer. |
| `ECX` | `R12D` at `0x15ebf5d` | First destination slot. `0x15ec0f7` increments it once per array element. |
| `R8` | `[RBP-0x50]` at `0x15ebf59` | Optional dword array. Null becomes a stored 0 (`XOR ECX, ECX` at `0x15ec0b5`). Otherwise `[R8 + R15]` at `0x15ebfef`. |
| `R9` | `[RBP-0x58]` at `0x15ebf55` | Optional dword array. Null selects the header itself (`CMOVZ` at `0x15ec0cf`), and the stored dword is then `[header+0]`. Otherwise `[R9 + R15]`. |

The decompiler prints the same six parameters. It does not by itself prove the roles in the right-hand column.

## What the body does

1. If `[master+0x70]` is non-null (`0x15ebf6e`, `TEST`/`JZ` at `0x15ebf72`/`0x15ebf75`) and the signed dword at `[shader+0x18]` is greater than 0 (`0x15ebf77`–`0x15ebf7d`), a loop calls through the qword at `0x2253678`. `0x15ebf8f` is `LEA R13, [0x102253678]` and `0x15ebf9d` is `CALL qword ptr [R13]`. The argument is the dword at `[shader+0x10] + (n*0x28 - 0x1c)`, then that offset minus `0x28`, `n` times. For `n = 1` the only offset is `0x0c`.
2. For each of `count` elements, starting at slot `ECX`: a null header takes the path at `0x15ebff8`. A header whose dword at `+8` is 0 (`0x15ec0b7`) skips the stores. Otherwise `0x15ec0c7` writes the header pointer at `[master + slot*8 + 0x130]`, `0x15ec0d3` writes the offset dword at `[master + slot*4 + 0x150]`, and `0x15ec0dc` writes the third dword at `[master + slot*4 + 0x160]`.
3. If that slot index is 0 (`TEST R12D` at `0x15ec0e3`), `0x15ec0eb` writes `[header+4]` to `[master+0x64]` and `0x15ec0f0` writes the same third dword to `[master+0x6c]`.
4. `0x15ec103` always stores `1` at `[master+0x170]`, including when the count is 0.

Slots outside this call are not cleared. A header with a zero dword at `+8` does not replace the previous slot.

`ghidra_query address 0x2253678` is present, defined `undefined8`, symbol `___glewDisableVertexAttribArrayARB`, `initialized` false, `value_kind` `unavailable`. A `WRITE` from `_glewContextInit` at `0x15d907f` fills it at runtime. The static image does not contain the function pointer. The same slot is read by `_GfxBeginScene` at `0x15ea71f`.

The null-header path reads `bOutputError` at `0x2238580` (`0x15ebff8`) and, if it is 0, calls `0x1855b1e`, `0x1855c6e`, `0x1855d06`, `getloc`, `use_facet`, a virtual call at `0x15ec06a`, `~locale`, `put`, `0x1618360`, and `0x185557c`, then stores `1` at `0x2238580` (`0x15ec098`). `address 0x2238580` is an uninitialized `undefined1` named `bOutputError`, referenced only by those two instructions. `address 0x1bb6a90` is an initialized C string whose first bytes are `2f55736572732f70647861646d696e2f` (`/Users/pdxadmin/`). The decompiler names a second literal, at the `LEA` `0x15ec02d` (`0x1bb6bb5`), as the bad-buffer line. Those bytes were not read back here.

## Effect classification

- Shader-attribute calls through `0x2253678`: deferred. The recorded state is the dword arguments of that loop and the byte stored at `[master+0x170]`. The consumer is the later draw path that already treats `[master+0x170]` as pending attribute setup. This replacement does not call the slot. The pointer after GLEW initialization is unknown.
- Master slot pointer, offset dword, third dword, the two slot-0 dwords, and `[master+0x170]`: implemented as engine-memory writes.
- Header dword `+8 == 0` skips that slot: implemented. `eu4_gfx_create_vertex_buffer` stores 0 at `+8`, so a buffer fresh from that call is not selected until something stores a non-zero dword there.
- Null-header flag at `0x2238580`: implemented. The logger and locale calls are unresolved. Nothing else references the flag. They are not called.
- Metal resource selection: implemented as a header per slot, resolved through the existing vertex registry on read. The original function does not bind a GL buffer. The registry lookup is the stand-in that lets a later encoder see the current `MTLBuffer`.

## Metal state

Four kinds of value are kept, and none of them is a copied `MTLBuffer`:

- The engine header pointer for each slot that this call actually stored.
- The deferred attribute-index list from the disable loop.
- The master bytes listed above, written only where the instructions write them.
- A read resolves the header through the registry, so an update that replaces the allocation is visible without touching the selection again.

Destroy and backend release drop selections whose header they drop. They do not write the master.

Safety policy, not from the instructions: a null handle or a null master stops with `master` instead of faulting. A negative count, or a slot whose `+0x130`, `+0x150`, or `+0x160` store would fall outside the `0x198`-byte master, stops with `range` before any store. A shader attribute count above 32 stops with `attrib` before any store. The call runs on the thread bound at `_GfxInit`, like the other buffer replacements; a different thread stops with `thread`.

## Open assumptions

- The dword at `header+0` is whatever the header stores there. In this backend's create path that dword is the stride. The instructions only show a 4-byte load.
- The dword at `header+4` is likewise only a 4-byte load. Create stores the element count there.
- The dword at `header+8` is only a zero test. It is not shown to be a GL name by these instructions alone.
- The object at `[master+0x70]` is only known as the base for `+0x10` and `+0x18`. Calling it a shader is the decompiler's neighbor evidence (`___glewDisableVertexAttribArrayARB`), not a type record.
- Whether a draw consumes the disabled attribute indices themselves, or only the `[master+0x170]` byte, is not settled by this function.
