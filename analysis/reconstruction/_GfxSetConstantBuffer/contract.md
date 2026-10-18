# RE0 — `_GfxSetConstantBuffer` function contract

**Status:** RE0 complete for this target. This is a *contract*, not reconstructed source:
it records what the original machine code does to declared, observable memory, so that a
reconstruction and a differential test can be built against it. It makes no performance
claim and establishes no runtime behaviour.

**Scope:** offline, static. Nothing here launches EU IV, opens a game process or attaches
to a binary.

## Identity and provenance

| Field | Value |
| --- | --- |
| Symbol | `_GfxSetConstantBuffer` (`namespace: Global`, `tier: core`, `domain: backend`) |
| Entry RVA / VA | `0x15ec36a` / `0x1015ec36a` |
| Body range | `0x15ec36a`–`0x15ec3b5` (half-open); 21 instructions, 75 decoded bytes |
| Image base | `0x100000000` |
| Binary | GOG EU IV 1.37.5 x86-64, SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` (pinned in `reverse_engineering/target.json`) |
| Corpus | baseline `auto-v1-20261013`; sealed renderer snapshot `gfx-batch-00044.zip:static.jsonl` |
| Instruction listing | [`instructions.json`](instructions.json) (addresses, lengths, mnemonics, operands) |
| Callers / callees | 37 selected direct callers, 0 selected direct callees (`analysis/renderer-corpus/scope/v1/renderer-functions.jsonl`) |
| Decompiler hypothesis | `void _GfxSetConstantBuffer(void **, int *, ...)`; used only as a cross-check, never as evidence |

**Evidence chain.** The pinned binary bytes → the sealed `static.jsonl` row (whose
instruction bytes the snapshot receipt recomputes from the binary) → the instruction
listing → this contract. The listing's own columns were additionally hand-decoded from the
raw bytes as a decode-independent cross-check (see *Behaviour*).

`tests/test_reconstruction_contract.py` re-derives the listing from the snapshot and verifies
every instruction's bytes against the pinned binary via the repository's `MachOMapper` when the
local corpus is present; in CI (no corpus) it checks the listing's internal consistency only.
**Local verification (2026-10-18):** with the corpus present, all six tests passed, including
both `MachOMapper` byte checks against the pinned binary at
`/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4`. That local result is not
reproduced in CI and is recorded here as the qualification for it.

## ABI

System V x86-64. Only four of the argument registers are read:

| Register | Role | Used? |
| --- | --- | --- |
| `RDI` | pointer to a pointer — the Gfx context handle (`ctx = *RDI`) | yes, dereferenced |
| `RSI` | the constant-buffer pointer that is *stored*; its first `int` is the byte length when `R9D == -1` | yes |
| `RDX` | — | no |
| `RCX` | slot index (`uint`), zero-extended (`MOV ECX,ECX`) | yes |
| `R8` | — | no |
| `R9` (`R9D`) | signed length selector: `-1` means "read the length from `[RSI]`" | yes |

Return value: **unresolved.** The instructions establish only that `RAX` holds the context base
when the function returns; they do **not** show that callers ignore it — a `void` function may
leave anything in `RAX`. Whether the real return type is `void`, or a value the 37 callers
consume, needs a caller-liveness audit that RE0 has not done. Until then the differential
contract treats `RAX` as an **observed register outcome to record**, not a declared return.
`RBP` is saved and restored; no stack frame other than the saved `RBP`. `RDX` and `R8` are
unused by this body — whether they are genuinely absent from the prototype or merely dead here
is likewise **unresolved** (see *Uncertainties*).

## Behaviour, from the instructions

Register flow (lines are RVAs from `instructions.json`):

1. `0x15ec36a` `PUSH RBP`; `0x15ec36b` `MOV RBP,RSP` — frame pointer only.
2. `0x15ec36e` `MOV RAX,[RDI]` → `ctx = *RDI` (the context object).
3. `0x15ec371` `MOV ECX,ECX` → `slot` is a 32-bit unsigned index.
4. `0x15ec373` `CMP R9D,-1` / `0x15ec377` `JZ 0x15ec38d`.
5. **`size == -1` branch** (`0x15ec38d`): `MOV R9D,[RSI]` (length read from the buffer's
   first dword); `ADD R9D,3`; `SHR R9D,2` → `words = (*(int*)buffer + 3) >> 2` (unsigned).
6. **otherwise** (`0x15ec379`–`0x15ec38b`): `EDX = R9D + 3`; `R9D += 6`; `TEST EDX,EDX`;
   `CMOVNS R9D,EDX`; `SAR R9D,2`. This is the compiler's signed divide-by-4-toward-zero
   idiom applied to `R9D`:
   `words = ((int32)(R9D + 3) >= 0 ? (R9D + 3) : (R9D + 6)) >> 2` (arithmetic shift).
7. **store** (`0x15ec398`): `SHL RCX,4` (×16 stride), then three writes to `ctx`:
   - `0x15ec39c` `MOV dword [RAX+RCX+0xc8],R9D` → `ctx.word_count[slot] = words`
   - `0x15ec3a4` `MOV qword [RAX+RCX+0xd0],RSI` → `ctx.buffer[slot] = buffer`
   - `0x15ec3ac` `MOV byte [RAX+0x128],1` → `ctx.dirty = 1`
8. `0x15ec3b3` `POP RBP`; `0x15ec3b4` `RET`.

**Manual decode cross-check.** Each of the 21 byte groups decodes by hand to the listed
mnemonic (e.g. `55`=`PUSH RBP`; `48 8b 07`=`MOV RAX,[RDI]`; `44 89 8c 08 c8 00 00 00`
= `MOV [RAX+RCX+0xc8],R9D` with SIB base `RAX`, index `RCX`, disp32 `0xc8`; `c6 80 28 01
00 00 01`=`MOV byte [RAX+0x128],1`). The lengths sum to exactly 75 and tile the body range
with no gap. This is a decoder-independent confirmation of the listing.

## Object layout (evidence-derived)

Writes are relative to `ctx = *RDI`, with a 16-byte stride indexed by `slot`:

| Offset | Width | Access | Meaning (hypothesis from use) |
| --- | --- | --- | --- |
| `ctx + 0xc8 + slot*16` | 4 | write | word count of the bound buffer |
| `ctx + 0xd0 + slot*16` | 8 | write | the bound buffer pointer |
| `ctx + 0x128` | 1 | write `1` | dirty flag for the constant-buffer binding set |

The two slot arrays are adjacent (stride 16 with an 8-byte field at `+0` and an 8-byte
pointer at `+8`), consistent with `struct { uint32 word_count; uint32 pad; void *buffer; }`.
The pad is not written. The field *names* are hypotheses; the offsets, widths and the
16-byte stride are directly evidenced.

## Observable domain (the differential contract)

**Initial test domain: `slot ∈ [0, 6)`** — see *Slot range* below for why 6, and for what
happens outside it. For those inputs a correct reconstruction must reproduce, from the same
inputs and the same initial `ctx`:

1. `*(uint32 *)(ctx + 0xc8 + slot*16)` after the call;
2. `*(void **)(ctx + 0xd0 + slot*16)` after the call;
3. `*(uint8  *)(ctx + 0x128)` after the call;
4. **preservation** — no other byte of the declared observation region changes;
5. the register outcome — `RAX` (see the ABI section: recorded, not a declared return).

**Declared observation region:** `[ctx + 0xc8, ctx + 0x129)` — the two adjacent arrays and the
dirty byte. For `slot ∈ [0, 6)` every indexed write falls inside `[ctx + 0xc8, ctx + 0x128)`,
and the flag write is `ctx + 0x128`, so the region is closed under the declared domain. The
differential harness extends it with canary bytes on both sides. The stack (the saved `RBP`) is
excluded: it is restored before `RET`.

### Slot range

`slot` is not bounds-checked by the function, so the region is **not** closed for every value.
The recovered offsets give the bound directly:

    0xc8 + 6 * 0x10 = 0x128        (six 16-byte slots, then the dirty byte)

`slot = 6` makes the first indexed write land **on** the dirty byte and the second extend past
the region entirely. That arithmetic is consistent with a six-entry slot array immediately
followed by the flag — `struct { Slot slots[6]; uint8 dirty; }` with `Slot` =
`{ uint32 words; uint32 pad; void *buffer; }` — but the array's declared length is **not**
established here; it is a caller invariant.

- The pilot tests `slot ∈ [0, 6)`. Those are the values for which the three fields are
  independent, which is what makes the differential contract well posed.
- `slot ≥ 6` is **not** tested and must **not** be treated as impossible. The aliasing is
  recorded, and the valid range is an open question for a caller-side audit (the array length,
  and whether any of the 37 callers passes ≥ 6).

## Dependency map

| Dependency | Evidence |
| --- | --- |
| Direct calls | **none** — no instruction has `machine_call`, `pcode_is_call` or `flow_type_is_call` |
| Indirect calls | **none** — no computed flow, no `pcode_kind` other than `none` |
| RIP-relative data | **none** — `references_from` holds only the two jump targets (`CONDITIONAL_JUMP` → `0x15ec38d`, `UNCONDITIONAL_JUMP` → `0x15ec398`); every memory operand is register-based |
| Globals / TLS | **none** — no absolute or segment-relative access |
| External state | `*RDI` must be a readable pointer to a writable context; `RSI` must be readable **only** in the `size == -1` path; `ctx` must be writable at the three offsets (and, for arbitrary large `slot`, beyond `0x128` — the function performs **no bounds check**) |
| Control flow | two branches only: the `size == -1` test and the store join. No loop. No exceptions. |

This is a **leaf** function: the only state it touches is the caller-supplied context and
buffer. There is no hidden global dependence to stub, which is why it is a suitable first
offline-execution target.

## Uncertainties

- **Return register.** `RAX` holds `ctx` at `RET`; whether callers consume it — i.e. the true
  return type — is unresolved pending a caller-liveness audit, and is treated as a recorded
  outcome rather than a declared return.
- **`RDX`/`R8` are unused.** The prototype may have more parameters (e.g. a shader stage or a
  byte-stride) that this particular implementation ignores, or the scope's caller set may
  pass fewer. Recorded; not needed for the declared observable domain.
- **Field names and the `+0x128` flag's meaning** are inferred from use, not proven.
- **The slot array's length.** Six is inferred from `0xc8 + 6*0x10 = 0x128`, not established by
  the function, which does not check it (see *Slot range*).
- **The length arithmetic** is stated at the instruction level (above); a C++ reconstruction
  must match the truncation/rounding exactly rather than the decompiler's rendering of it.

## Why this target, and what it does not establish

Chosen for **tractability and observability**, not payoff: 21 instructions, 0 callees, no
globals, three declared writes. The 37 callers make it a useful gateway into the state
machine once the first differential test passes.

It does **not** establish that its callers are understood, that OpenGL state is reproduced,
or that tuning this setter has any performance effect. It is the first link in the
reconstruction chain, not an optimisation claim.

## Next

A bounded offline execution pilot (Ghidra P-code) must show the original instructions
executing under an explicitly constructed state and producing the three declared writes
**and the recorded register outcomes (`RAX`)**, for `slot ∈ [0, 6)`, with results checked
against this contract — not solely against the decompiler or a reconstruction.
