# RE0 — `_GfxForceWireframe` function contract

**Status:** RE0→RE1 **PASS** for this function on static evidence (2026-10-25).
This is a contract, not reconstructed source. The hand-off plan (fixture, harness,
replay adapter, A2 closure decision) is [`package.md`](package.md). G2 has not started.
Nothing here is a P-code run, a native execution, or a live game observation.

**Scope:** offline, static. Nothing here launches EU IV, opens a game process, or
attaches to a binary.

## Identity and provenance

| Field | Value |
| --- | --- |
| Symbol | `_GfxForceWireframe` (`namespace: Global`, `tier: core`, `domain: backend`) |
| Entry RVA / VA | `0x15ed175` / `0x1015ed175` |
| Body range | `0x15ed175`–`0x15ed182` (half-open); 5 instructions, 13 decoded bytes |
| Image base | `0x100000000` |
| Binary | GOG EU IV 1.37.5 x86-64, SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` (pinned in `reverse_engineering/target.json`, read only) |
| Body SHA-256 | `45957994d8a114d2be65e430e66d98c04f4efa818180cbc1fe274fb75b3b3dae` (the 13 body bytes) |
| Corpus | baseline `auto-v1-20261013`; sealed renderer snapshot `gfx-batch-00044.zip:static.jsonl` |
| Instruction listing | [`instructions.json`](instructions.json) |
| Frozen contract baseline | `8632d66b8c9c977ec6f03eac76767a4800eb7ebc` |
| Callers / callees | 4 selected direct callers, 0 selected direct callees (`analysis/renderer-corpus/scope/v1/renderer-functions.jsonl`). The sealed caller rows enumerate 7 direct sites in those 4 functions (below) |
| Decompiler hypothesis | local index pseudocode renders `void _GfxForceWireframe(long, undefined1)` as one byte store. Hypothesis only. The committed evidence is the instruction row and the pinned bytes |

`reverse_engineering/target.json` `code_windows` lists `_GfxSetConstantBuffer` only.
This package records the body digest here and does not edit that file. G2 cannot
assume `code_window("_GfxForceWireframe")` succeeds until a later amendment adds
the window.

**Evidence chain.** Pinned binary bytes → sealed `static.jsonl` row (status
`success`) → this listing → this contract. On 2026-10-25, `MachOMapper` plus
`verify_static_row_bytes` checked all 5 instructions of the sealed row against
`/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` after
`assert_pinned_executable`. Result: `(5, 13)`. The body digest above is
SHA-256 of those 13 bytes. That local check is not what
`tests/test_reconstruction_contract.py` runs; that checker covers
`_GfxSetConstantBuffer` only. Decompiler text was not used as evidence.

## ABI

System V x86-64. The body reads two registers:

| Register | Role | Used? |
| --- | --- | --- |
| `RDI` | base of the stored byte | yes |
| `RSI` (`SIL`) | the stored value, low 8 bits | yes |
| `RDX`, `RCX`, `R8`, `R9` | — | no |
| `RAX` | — | not written |

`RBP` is saved and restored. There is no other stack frame. The instructions do
not write `RAX`. Whether any caller reads `RAX` after the call is unresolved, so
the differential contract records `RAX` as preserved by this body, and does not
declare a return type.

## Behaviour, from the instructions

Straight-line body. RVAs are from [`instructions.json`](instructions.json).

1. `0x15ed175` `PUSH RBP`; `0x15ed176` `MOV RBP,RSP` — frame pointer only.
2. `0x15ed179` `MOV byte ptr [RDI + 0x194],SIL` — the only memory write.
3. `0x15ed180` `POP RBP`; `0x15ed181` `RET`.

**Manual decode cross-check.** `55` = `PUSH RBP`. `48 89 e5` = `MOV RBP,RSP`.
`40 88 b7 94 01 00 00` = `MOV r/m8, r8` with a REX prefix (so the reg field is
`SIL`, not `DH`), ModRM `b7` (disp32, base `RDI`), disp32 `0x194`. `5d` = `POP
RBP`. `c3` = `RET`. Lengths 1+3+7+1+1 = 13 and tile `0x15ed175`–`0x15ed182`
with no gap. The sealed row's `bytes_hex` is `554889e54088b7940100005dc3`.

Every instruction has `machine_call`, `pcode_is_call`, `flow_type_is_call`,
`census_call`, and `flow_is_computed` false, `pcode_kind` `none`, and
`references_from` empty. Those flags are the sealed row's, checked against the
pinned bytes by the local verification above. They are static facts about the
decoded instructions, not a P-code execution.

## Object layout (evidence-derived)

The only access in this body is relative to `RDI`:

| Offset | Width | Access | Meaning |
| --- | --- | --- | --- |
| `RDI + 0x194` | 1 | write | the byte copied from `SIL`. The field name is not established |

No other offset is read or written. The object type, the object's length, and
any field beside this byte are not established by this function. Callers load
the pointer from `[base + 0x80]` (below). That is caller context. It does not
add a field to this layout.

The next function in the image is `_GfxGetCaps` at `0x15ed182`. Its sealed row
reads `dword [RDI + 0x18f]` and `qword [RDI + 0x188]` and writes through `RSI`.
Those accesses end at `0x193`. They do not include `0x194`. This package does
not establish that any caller passes the same pointer to both functions.

## Observable domain (the differential contract)

A correct reconstruction, given the same `RDI` and the same `SIL`, must reproduce:

1. `*(uint8 *)(RDI + 0x194)` after the call, equal to the entry `SIL`;
2. preservation of every other byte in the declared observation region;
3. `RBP` restored;
4. `RAX` unchanged by this body (recorded outcome, not a declared return).

**Declared observation region:** `[RDI + 0x194, RDI + 0x195)`. The offset is a
constant. The function has no index and no second store, so the region is closed
for every `SIL`. A harness extends it with canary bytes on both sides; the canary
width is part of the fixture plan in [`package.md`](package.md), not a second
write this function performs.

**Preconditions.** `RDI + 0x194` is writable for one byte. The body has no null
check and no range check. `SIL` is stored as a byte; the body does not inspect
the upper bits of `RSI`.

**Postconditions.** The byte at `RDI + 0x194` equals entry `SIL`. No other memory
operand exists. The stack slot used by `PUSH RBP` is restored before `RET`.

## Dependency map

| Dependency | Evidence |
| --- | --- |
| Direct calls | **none** — no instruction has `machine_call`, `pcode_is_call`, or `flow_type_is_call` |
| Indirect calls | **none** — `flow_is_computed` is false on every instruction; `pcode_kind` is `none` |
| RIP-relative data | **none** — `references_from` is empty on every instruction |
| Globals / TLS | **none** — no absolute or segment-relative access |
| External state | `RDI + 0x194` must be writable for one byte. The function does not allocate, free, or lock |
| Control flow | straight line. No branch, no loop, no call |

This is a leaf. The only state it touches is the caller-supplied object.

## Callers and context

Tracked scope: 4 selected direct callers, 0 selected direct callees
(`renderer-functions.jsonl`, entry `0x15ed175`). The sealed static rows of those
callers contain these direct sites. Each site's `RDI` / `ESI` setup is the
instruction immediately above the call in that row. Caller names are the sealed
row's `name_mangled`.

| Site | Caller (entry) | Setup evidenced at the site | Transfer |
| --- | --- | --- | --- |
| `0x149d0cb` | `CGraphics::Render2dObjectsToScreen` (`0x149d082`) | `MOV RDI, qword ptr [RBX + 0x80]`; `MOV ESI, 0x1`. `RBX` was set from entry `RDI` at `0x149d094` and is not rewritten before this site | `CALL` |
| `0x149d19c` | same | `MOV RDI, qword ptr [RBX + 0x80]`; `XOR ESI, ESI` | `CALL` |
| `0x149ec31` | `CGraphics::SetWireframe` (`0x149ec1e`) | `MOV byte ptr [RDI + 0x4bc], SIL` then `MOV RDI, qword ptr [RDI + 0x80]`. The `+0x4bc` store is the caller's. `SIL` is still the caller's incoming argument at the transfer. Sealed flags on the `JMP`: `flow_type_is_call` true, `flow_override` `CALL_RETURN`, `machine_call` false, reference `UNCONDITIONAL_CALL` → `0x15ed175` | tail `JMP` |
| `0x14c7df5` | `CPdxMeshObject::RenderBuckets` (`0x14c7da4`) | `MOV RDI, qword ptr [RBX + 0x80]`; `MOV ESI, 0x1`. `MOV RBX, RDI` is at `0x14c7dcf` | `CALL` |
| `0x14c8469` | same | `MOV RDI, qword ptr [R14 + 0x80]`; `XOR ESI, ESI`. `R14` was loaded from a stack slot at `0x14c8457`. The package does not name that slot's object | `CALL` |
| `0x14d4831` | `CPdxParticleObject::RenderBuckets` (`0x14d475e`) | `MOV RDI, qword ptr [RAX + 0x80]` after `MOV RAX, qword ptr [RBP - 0x50]`; `MOV ESI, 0x1` | `CALL` |
| `0x14d566f` | same | `MOV RDI, qword ptr [RAX + 0x80]` after `MOV RAX, qword ptr [RBP - 0x50]`; `XOR ESI, ESI` | `CALL` |

**What the caller rows establish.** Seven sites pass a pointer loaded from offset
`0x80` of some base, and they pass `ESI` of `0` or `1` (the `SetWireframe` tail
call passes the incoming `SIL` through). Several sites are dominated by a compare
of a global byte (`0x1022518b0`, `0x102217de2`, `0x102217de4`) that skips the
call. Those globals are the callers' branches. This function does not read them.

**What they do not establish.** Thread identity. That the four `+0x80` pointers
are one type. That `ESI` is only ever `0` or `1` — the body stores any byte, and
seven sites are not a domain proof. That `SetWireframe`'s store at `CGraphics +
0x4bc` is part of this function. A lock, a TLS slot, or a GL call inside this body.

## Ordering

The body has one store, then the epilogue, then `RET`. It imposes no order on
GL, on other setters, or on the callers that bracket a draw with `ESI = 1` and
later `ESI = 0`. That bracket is caller control flow.

## Uncertainties

- **Object type and length.** The body shows a one-byte store at a fixed offset.
  It does not show the allocation, the type, or any other field.
- **Return register.** `RAX` is not written. Caller use of `RAX` is unreviewed.
- **`RDX` / `RCX` / `R8` / `R9` are unread.** The prototype may have more
  parameters that this body ignores.
- **The byte's meaning.** The symbol is `_GfxForceWireframe`. The instructions
  store `SIL`. They do not show a GL polygon-mode call. Callers that pass `0`
  and `1` do not prove the body masks the byte.
- **Shared identity of `[base + 0x80]`.** The load pattern repeats. A single
  device object is a hypothesis, not a result of this contract.
- **Thread and reentrancy.** No lock and no TLS in this body. Concurrent calls
  would race on the byte the same way the original does. The package does not
  assign the callers to a thread.

## Selection and the RE0→RE1 gate

The gate is the one in
[`docs/renderer-reconstruction-and-differential-validation.md`](../../../docs/renderer-reconstruction-and-differential-validation.md):
identified callable boundary; finite, recoverable input/side-effect surface;
credible strategy for calling or observing the original; no unresolved
ownership, context, or aliasing that makes a faithful replacement unsafe.

`_GfxSetConstantBuffer` already passed it and is the first package. This
selection walks the remaining ranked candidates. Rank is the same test used for
that function: small body, no unresolved call, a side effect that stays inside
a closed memory region. Scope counts come from
`analysis/renderer-corpus/scope/v1/renderer-functions.tsv`. Instruction facts
below come from the sealed `static.jsonl` rows, in the batch named for each
function. Rows were not executed.

### `InternalSetTextureAndSamplerState` — FAIL

Entry `0x15ecce2`, body through `0x15ece13`, 84 instructions, 305 bytes,
`gfx-batch-00044.zip`. One selected caller (`_GfxSetTextures`). The row contains:

- `0x15eccf9` `LEA RAX, [0x102252d68]` (`references_from` DATA `0x2252d68`), then
  `0x15ecd06` `CALL qword ptr [RAX]` with `flow_is_computed`, `machine_call`,
  `pcode_is_call`, `flow_type_is_call`, and `pcode_kind` `CALLIND`. The scope row
  records that site as a missing indirect.
- Direct `CALL`s at `0x15ecd20` → `0x18c25b8`, `0x15ecd3f` → `0x18c268a`,
  `0x15ecd75` / `0x15ecd84` / `0x15ecd93` / `0x15ecdad` / `0x15ecdbe` /
  `0x15ecdcf` / `0x15ecde0` → `0x18c269c`, and `0x15ecdf8` → `0x18c2696`
  (ten direct calls). Immediates in that body include `0xde1`, `0x8513`,
  `0x8500`, `0x8501`, `0x813a`, `0x813b`, `0x813d`, `0x2800`, `0x2801`,
  `0x2802`, `0x2803`, and `0x84fe`. Naming them as GL enumerants is a
  hypothesis. The calls themselves are the evidence.
- Reads of `[R14 + …]` and `[R12 + …]`, and one store `0x15ece01` `MOV dword ptr
  [R14], EAX`.

The store is finite. The indirect call and the ten direct external calls are
not a closed memory surface. A faithful replacement would have to reproduce an
unresolved function pointer and those callees. That is an ownership and context
requirement the static row does not close, and it would need an effects model
this recorder does not have. **FAIL.** Scope stays stopped: no object-graph
trace and no new recorder.

### `_GfxDrawIndexed` — FAIL

Entry `0x15eaa77`, body through `0x15eac6f`, 124 instructions, 504 bytes,
`gfx-batch-00043.zip`. The scope row records 6 selected direct callees, 2
`pcode_callind` sites, and 2 missing indirects. The sealed row's call-like
transfers include:

- `0x15eaaf4` `CALL qword ptr [R13]` (`CALLIND`, computed);
- `0x15eab94` `CALL qword ptr [RCX + 0x38]` (`CALLIND`, computed);
- direct `CALL`s at `0x15eab2b`, `0x15eab4f`, `0x15eab5e`, `0x15eab74`,
  `0x15eab84`, `0x15eab9e`, `0x15eabaa`, `0x15eabb2`, `0x15eabbe`, `0x15eac08`;
- `0x15eac48` `JMP RAX` (computed);
- `0x15eac6a` `JMP 0x1018c2606` with `flow_override` `CALL_RETURN`.

The body also reads `byte ptr [R12 + 0x128]` at `0x15eaaa7`. That is a read of
the same offset `_GfxSetConstantBuffer` writes. It does not make this function's
side effects closed. The surface includes unresolved indirect calls and a
computed jump. **FAIL.** Treating it as the second function would expand into
draw submission and an unbounded graphics-state graph.

### Nearby Gfx setters

Address-near the first function, in the same `_Gfx*` backend run. Empty bodies
fail observability. The first body with a closed side effect is this function.

| Function | Entry | Instructions | Gate result on the sealed row |
| --- | --- | --- | --- |
| `_GfxSetGamma` | `0x15ed0e2` | 4 (`PUSH RBP` / `MOV RBP,RSP` / `POP RBP` / `RET`), 0 calls, `gfx-batch-00044.zip` | **FAIL.** No argument is read and no memory is written. There is no side-effect surface |
| `_GfxSetAllowResourceCreation` | `0x15ed1af` | same 4-instruction empty body, `gfx-batch-00044.zip` | **FAIL** for the same reason. 0 selected callers |
| `_GfxForceWireframe` | `0x15ed175` | 5, 0 calls, one byte store | **PASS** (this contract) |
| `_GfxSetPrimitiveType` | `0x15ea76f` | 12, plus callee `GetOpenGLPrimitiveType` at `0x15edff7` | Not selected. The setter stores `EAX` to `[ctx + 0x8]` after `CALL 0x15edff7`. The callee reads a RIP-relative table at `0x1a79600` (`LEA` at `0x15ee003`). That is a second pin and a callee. Tractability stops at the smaller leaf |
| `_GfxSetViewPort` | `0x15ea4f8` | 13 | Not a closed surface. It writes `[RDI + 0xac]` (xmmword), `[RDI + 0xbc]`, and `[RDI + 0xc0]`, then tail-`JMP`s to `0x18c26ae` (`flow_override` `CALL_RETURN`). The external transfer is unresolved |
| `_GfxSetIndexBuffer` | `0x15ec28e` | 18 | **FAIL** as a candidate. `0x15ec2af` is `CALL qword ptr [RAX]` (`CALLIND`) through `0x2253610`, with immediate `EDI = 0x8893`. The later store `MOV dword ptr [R14 + 0x68], EAX` does not close the indirect call |
| `_GfxSetScissorRectangle` | `0x15ed0aa` | 18 | Not a closed surface. It computes arguments and tail-`JMP`s to `0x18c2672`. The effect leaves the function |
| `_GfxSetTextures` | `0x15ecbf2` | 81 | **FAIL** as a candidate. Counted loops call `0x15ecce2` (`InternalSetTextureAndSamplerState`) at `0x15ecc6d` and `0x15eccc6`, and they walk caller-supplied pointer arrays. The callee already failed |

Larger setters (`_GfxSetShader`, `_GfxSetRenderTarget`, `_GfxSetBlendState`, and
the depth/rasterizer setters) have selected-callee or callsite counts above this
leaf in `renderer-functions.tsv`. Their bodies were not characterized. They are
not the next candidate once this leaf passes.

### Written gate, criterion by criterion

The gate is this sentence, unchanged:

> **Gate RE0 → RE1:** identified callable boundary; finite, recoverable
> input/side-effect surface; credible strategy for calling or observing the
> original; no unresolved ownership/context requirements likely to make a
> faithful replacement unsafe.

It is the sentence that applied to `_GfxSetConstantBuffer` at
`5fdc728` ("RE0: instruction-level contract for `_GfxSetConstantBuffer`").
That commit's `target.json` held the image SHA-256, the default executable,
and the image base. It had no `code_windows` key. That key arrived in
`f4fce9c` ("RE2: authenticate the reference's instruction bytes and its use").
The original contract's *Next* section still named the P-code pilot as future
work. The four criteria below are met on the same kind of static evidence.
Nothing in *Not established* is promoted to a P-code, native, or live tier.

1. **Identified callable boundary.** Sealed row in `gfx-batch-00044.zip`,
   status `success`, entry `0x15ed175`, `body_ranges` end `0x15ed182`.
   [`instructions.json`](instructions.json) tiles that half-open range with 5
   instructions and 13 bytes. On 2026-10-25, `assert_pinned_executable` then
   `verify_static_row_bytes` against the executable named in `target.json`
   returned `(5, 13)`; the body SHA-256 is
   `45957994d8a114d2be65e430e66d98c04f4efa818180cbc1fe274fb75b3b3dae`. The
   image pin is the SHA-256 already in `target.json`, the same pin the first
   RE0 contract cited. The address range lives in this contract and in
   `instructions.json`, which is where `5fdc728` put
   `_GfxSetConstantBuffer`'s range. The next scope row, `_GfxGetCaps`, starts
   at `0x15ed182`.
2. **Finite, recoverable input/side-effect surface.** The body reads `RDI`
   and `SIL`. The only memory operand is `0x15ed179` `MOV byte ptr [RDI +
   0x194],SIL`. Every sealed instruction has the call flags false,
   `pcode_kind` `none`, and `references_from` empty. The declared region
   `[RDI + 0x194, RDI + 0x195)` is closed for every `SIL` because the offset
   is a constant.
3. **Credible strategy for calling or observing the original.** The observable
   is that one byte plus adjacent canaries under constructed state, with `RDI`
   aimed at a writable object and `SIL` set. That is the constructed-state
   strategy the first RE0 contract named before its pilot existed. The
   strategy is credible because the memory operand is one fixed store and the
   body has no callee to stub. It has not been executed. The plan is in
   [`package.md`](package.md). Executing it would be new evidence, and this
   reconciliation does not add any.
4. **No unresolved ownership or context requirement that makes a faithful
   replacement unsafe.** The body does not allocate, free, lock, or call, and
   it has no global and no TLS access. It writes one byte and reads nothing
   else, so the body contains no second access that could alias that byte.
   The seven caller sites load `RDI` from `[base + 0x80]`. `SetWireframe`'s
   store at `[RDI + 0x4bc]` is at `0x149ec22`, in the caller, before it
   replaces `RDI`. The precondition matches the shape of the first contract's
   external-state line: the caller-supplied pointer must be writable at the
   declared offset. Unknown object type, unknown thread, and the absence of a
   GL call stay in *Not established*. The first RE0 contract likewise left
   field names unproven, left its callers ununderstood, and stated that it
   does not establish OpenGL state. Those gaps did not add a hidden write,
   and neither do these.

`tests/test_reconstruction_contract.py` checks `_GfxSetConstantBuffer` only.
Its 6 passing tests are not evidence for any criterion above. The byte
evidence for criterion 1 is the `verify_static_row_bytes` result in this
section.

A `code_windows` entry is not one of the four criteria. `target.json` stays
as it was at the first function's RE0 admission, plus the later
`_GfxSetConstantBuffer` window this package does not touch. Leaving this
function out of `code_windows` is recorded under *Not established*. It is not
a contract conflict: the function is expressed by the same RE0 package shape
`5fdc728` used (contract, instruction inventory, image SHA-256 already in
`target.json`).

**RE0→RE1: PASS.** G2 is not started. RE1 source, the P-code pilot, the
replay adapter, and the per-function A2 closure branch are not in this
package.

## Not established

- P-code execution, native execution of the 13 bytes, or any live EU IV run.
- A qualified fixture, a harness, or support in `oracle.function.replay`.
- The object's type, allocator, lifetime, or any field other than the one byte.
- That `[base + 0x80]` is one type across the four callers.
- Thread identity, or a proof that `SIL` is only `0` or `1` in the program.
- A GL wireframe effect. The body does not call GL.
- A return type.
- A `code_windows` entry in `reverse_engineering/target.json`. That key is
  the RE2 authenticator added in `f4fce9c`. It is outside the four-part
  RE0→RE1 gate. This package does not add it.
- Behaviour of the rejected functions beyond the defects cited above.
