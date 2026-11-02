# `_GfxDraw` contract

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Body `[0x15ea789, 0x15eaa77)`. The next symbol is `SShaderOpenGL::EnableVertexAttribArrays`. Prefix `55 48 89 e5 41 57`. These offsets are from this body. They are not the indexed-draw layout.

## Arguments

`rdi` is the handle. `(%rdi)` at `0x15ea7a3` is the master. `esi` is the incoming count. `edx` is saved to `r14d` at `0x15ea79d` and is not read from the master.

`testl %esi, %esi; jg` at `0x15ea7a6` keeps a positive `esi`. Otherwise `movl 0x64(%r12), %r15d` at `0x15ea7aa` replaces it. That dword is then the same register passed as the draw count, so it is a signed 32-bit value. No later instruction in this body tests it again.

## Missing shader

`movq 0x70(%r12), %rdi; testq; je` at `0x15ea7af` branches to `0x15ea830` when the qword is zero. That path compares the static byte `bOutputError` at `0x2238578`. When the byte is already 1 it returns at `0x15ea8ed`. When it is 0 it logs through `CLogger` and stores 1 to that static byte, then returns at the same instruction. It does not store to the master and it does not reach `glDrawArrays`.

## Pending state

A non-null shader with the byte at `+0x128` clear jumps to the attribute test at `0x15ea8f7`. When the byte is set, dwords at shader `+4` and `+8` must both be nonzero or the byte is cleared at `0x15ea8ee` and the upload is skipped. Otherwise six locations at shader `+0x1c` are walked with `rbx` equal to `0, 4, ..., 0x14`. A negative location is skipped. A non-negative location loads the pointer at master `0xd0(%r12,%rbx,4)` and the count at `0xc8(%r12,%rbx,4)`. Those six records occupy `[+0xc8, +0x128)`. A null pointer is skipped. Otherwise `callq *(%r13)` at `0x15ea806` is `___glewUniform4fvARB`, with the data pointer taken from the record `+8`. The byte at `+0x128` is then cleared.

The byte at `+0x170` is tested at `0x15ea8f7`. When it is set, `EnableVertexAttribArrays` is called at `0x15ea91a` with the shader, `master+0x130`, `master+0x150`, and `master+0x160`, and the byte is cleared. The helper walks four slots. Nothing in this body reads past `+0x170`.

## `glDrawArrays`

The tail at `0x15ea928` jumps to the `_glDrawArrays` stub at `0x18c2600`.

| Operand | Source |
| --- | --- |
| mode | dword at master `+8` |
| first | saved `edx` |
| count | positive `esi`, or the signed dword at `+0x64` |

A snapshot of `0x171` bytes covers `+0x170` and every earlier field this body reads. The vertex bytes, the uniform values, and the shader object are not inside that image.
