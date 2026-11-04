# `_GfxUpdateVertexBufferMultiple` research record

Machine evidence for the original function, written before any replacement. Sources are
`reverse_engineering.research` and `reverse_engineering.ghidra_query` against the existing
`EU4_RE_EXPORT` corpus. No per-function dossier and no `analysis/reconstruction/gfx-semantic/`
material was used.

## Entry

`python3 -m reverse_engineering.research resolve _GfxUpdateVertexBufferMultiple` is an exact
match in the renderer snapshot at entry RVA `0x15ebe5f`, batch `gfx-batch-00044`.

The body is the half-open range `0x15ebe5f` through `0x15ebf41`. The last instruction is
`RET` (`c3`) at `0x15ebf40`; the next byte `0x15ebf41` is the entry of
`_GfxSetVertexBuffers`. The body has 70 instructions.

The patchable entry prefix is the original eight bytes
`55 48 89 e5 41 57 41 56`. That is `PUSH RBP` at `0x15ebe5f`, `MOV RBP,RSP` at
`0x15ebe60`, `PUSH R15` at `0x15ebe63`, and `PUSH R14` at `0x15ebe65`; it ends on the
instruction boundary at `0x15ebe67`.

## Calling convention

System V AMD64. The prologue at `0x15ebe5f`-`0x15ebe6c` saves `RBP`, `R15`, `R14`, `R13`,
`R12`, and `RBX`, then executes `SUB RSP,0x18`.

| Register | Role from the instructions |
| --- | --- |
| `RDI` | Handle. `0x15ebe88` loads `[RDI]` into `RAX`, then `0x15ebe8b` saves that master pointer at `[RBP-0x38]`. |
| `RSI` | Vertex-buffer header. `0x15ebe70` copies it to `RAX`, `0x15ebe73` reads the dword at `[RSI+8]`, and `0x15ebe84` saves the header at `[RBP-0x30]`. |
| `RDX` | Array of range data pointers. `0x15ebe9b` copies it to `R15`; `0x15ebeba` reads `[R15 + R14*8]`, so each record is a qword pointer. |
| `RCX` | Array of range element counts. `0x15ebe81` copies it to `R12`; `0x15ebeb5` and `0x15ebed5` read `[R12 + R14*4]`, so each record is a dword. |
| `R8D` | Range count. `0x15ebe7e` copies it to `EBX`; `0x15ebea0` tests it and `0x15ebea2` skips the range loop when it is `<= 0`. |

The caller at `0x14d4cec` and `0x14d4d3f` also sets `R9D` and pushes a stack dword before
the call, but the body from `0x15ebe5f` through `0x15ebf40` never reads `R9` or that stack
argument. They are not parameters of this replacement.

## Memory

Reads in the body:

- `0x15ebe73` reads the dword at `[RSI+8]`. It is the only guard before all effects.
- `0x15ebe88` reads the master pointer from `[RDI]`.
- `0x15ebeab` and `0x15ebed3` read the header dword at `[header+0]` as the stride.
- `0x15ebeb5` and `0x15ebed5` read the range count dword at `[counts + i*4]`.
- `0x15ebeba` reads the range data pointer qword at `[data + i*8]`.
- `0x15ebeea` reads `[master+0x70]` as the shader pointer.
- `0x15ebef3` reads `[shader+0x18]` as the attribute count.
- `0x15ebf12` reads `[shader+0x10]` as the attribute-record base.
- `0x15ebf16` reads each attribute index dword from that base plus `0x28*n - 0x1c`.

Writes in the body:

- `0x15ebf2a` writes the byte `1` at `[master+0x170]` after a nonzero `[header+8]`.
- The indirect calls at `0x15ebecd` write GPU buffer memory, but they do not write the
  engine header, the count arrays, or the data arrays.
- No instruction in the body writes `[header+0]`, `[header+4]`, or `[header+8]`.

Branch conditions:

- `0x15ebe76`-`0x15ebe78` return immediately when `[header+8] == 0`.
- `0x15ebea0`-`0x15ebea2` skip the range loop when the signed range count is `<= 0`.
- `0x15ebee1`-`0x15ebee4` repeat the range loop while `R13 != R14`.
- `0x15ebeee`-`0x15ebef1` skip the attribute loop when `[master+0x70] == 0`.
- `0x15ebef3`-`0x15ebef9` skip the attribute loop when `[shader+0x18] <= 0`.
- `0x15ebf1d`-`0x15ebf28` repeat the attribute loop while the descending counter is
  greater than `1`.

## Range layout

The range records are split across two arrays, not a single struct array:

- `param_3` (`RDX`) is an array of 8-byte data pointers. `0x15ebeba` addresses record `i`
  as `[R15 + R14*8]`.
- `param_4` (`RCX`) is an array of 4-byte element counts. `0x15ebeb5` and `0x15ebed5`
  address record `i` as `[R12 + R14*4]`.
- `param_5` (`R8D`) is the number of records.

The byte offset is not supplied by the caller. `0x15ebeb0` clears `EBX`, `0x15ebeb2`
sign-extends it, and `0x15ebedc` adds `counts[i] * stride` after each range. The first range
therefore starts at byte offset `0`, and range `i` starts at
`sum(stride * counts[j])` for `j < i`. `0x15ebeb5` computes `stride * counts[i]`, which is
the byte size passed to the subdata call at `0x15ebecd`. Counts are element counts, not byte
counts.

The body reads the stride again for every record (`0x15ebeab` and `0x15ebed3`), so all
records in one call use the same header stride. A zero-size range advances the running
offset by zero.

## Ownership

The engine header is the object passed in `RSI` and saved at `[RBP-0x30]` by `0x15ebe84`.
The body reads its `+0` stride and `+8` token/name but never writes them. The master pointer
comes from `[RDI]` at `0x15ebe88`, and the only engine-memory write is `[master+0x170] = 1`
at `0x15ebf2a`.

In this Metal backend the header is owned by the create/destroy pair and is also the key of
the vertex registry. The registry entry strongly owns the `MTLBuffer`. This function is an
in-place update: it resolves the existing header, writes bytes into that same `MTLBuffer`,
and does not replace the buffer, change its length, or change the engine header. A released
entry keeps its header but has no `MTLBuffer`; the update must not recreate or adopt one.
Destroy removes the header from the registry; backend release only drops the buffer.

The `+8` dword is the body's presence/name token (`0x15ebe73`). A zero token returns before
`0x15ebe88`, so it neither binds nor writes the master flag. A nonzero token proceeds. The
`+4` dword is not read by this body.

## Indirect calls

All three calls are unresolved `CALLIND` records in the sealed xrefs.

- `0x15ebe8f` loads the slot address `0x2253610`; `0x15ebe96` sets `EDI = 0x8892`; the
  dword `[header+8]` is already in `ESI` from `0x15ebe73`; `0x15ebe9e` calls `[RAX]`.
  Ghidra names this slot `___glewBindBufferARB`. Its possible target is the GLEW
  `glBindBufferARB` entry.
- `0x15ebec6` loads the slot address `0x2253620`; `0x15ebebe` sets `EDI = 0x8892`,
  `0x15ebec3` sets `RSI` to the running offset, `0x15ebeb5` leaves the byte size in `EDX`,
  and `0x15ebeba` leaves the data pointer in `RCX`; `0x15ebecd` calls `[RAX]`. Ghidra
  names this slot `___glewBufferSubDataARB`. Its possible target is the GLEW
  `glBufferSubDataARB` entry.
- `0x15ebf0b` loads the slot address `0x2253678`; `0x15ebf16` puts an attribute index in
  `EDI`; `0x15ebf19` calls `[R12]`. Ghidra names this slot
  `___glewDisableVertexAttribArrayARB`. Its possible target is the GLEW
  `glDisableVertexAttribArrayARB` entry.

`ghidra_query address` reports all three slots as defined `undefined8`, `initialized` false,
`value_kind` unavailable. The static image does not hold the runtime function pointers.
Ghidra's data references show `_glewContextInit` writing them at `0x15d8edd`,
`0x15d8f14`, and `0x15d907f`. `research resolve` returns `not_found` for all three slot
addresses, so the slot names are Ghidra's symbols, not targets observed at runtime.

## OpenGL

The OpenGL effects recovered from the instructions are:

- Bind `GL_ARRAY_BUFFER` (`0x8892`) once to the name in `[header+8]` through the call at
  `0x15ebe9e`.
- For each range record, call `glBufferSubDataARB` target `0x8892` at the running byte
  offset derived by `0x15ebeb2`-`0x15ebedc` with size `stride * counts[i]` and the data
  pointer from `0x15ebeba`.
- Disable each shader vertex attribute array through the loop at `0x15ebf12`-`0x15ebf28`.
  The indices are read from the shader's attribute records using the same `0x28` stride and
  `-0x1c` first offset as `_GfxSetVertexBuffers`.
- Set `[master+0x170] = 1` at `0x15ebf2a`.

The Metal stand-in keeps those effects without a GL compatibility layer:

- The header is resolved through the existing vertex registry. Each range is copied into
  the existing shared `MTLBuffer` at exactly the same running byte offsets. No second bind
  is needed; later encoders obtain the current buffer from the slot registry.
- The buffer is updated in place. It is not replaced and its length is not changed, which
  matches the fact that the original only calls `glBufferSubDataARB`.
- The attribute-disable loop is recorded as deferred Metal state in the existing
  `g_disabled_attribs` list, exactly as the setter does. The future consumer is the draw or
  encoder path that reads `eu4_gfx_disabled_vertex_attrib*` together with the pending
  `[master+0x170]` byte. No shader compiler or command encoder is introduced here.
- The engine-visible `[master+0x170] = 1` write is preserved.

## Failure

The original body has no error path. A null `RSI` faults at `0x15ebe73`, a null `RDI`
faults at `0x15ebe88`, and null range arrays fault at `0x15ebeb5` or `0x15ebeba`. The
`<= 0` range count at `0x15ebea0` is not an error: it skips only the range loop and still
runs the attribute loop and writes `[master+0x170]` at `0x15ebf2a`.

Deliberate Metal safety policy for the replacement:

- A caller on a thread other than the `_GfxInit` thread stops with `thread` before any
  effect.
- A null header stops with `missing`. A zero `[header+8]` returns silently, matching
  `0x15ebe76`-`0x15ebe78`, and does not write the master flag.
- A nonzero-token header that is not in the vertex registry stops with `missing`; a
  registered header whose buffer was released stops with `released`.
- A null master pointer stops with `master`.
- A positive range count with a null data-pointer array or null count array stops with
  `range`.
- A negative stride or negative count, or a `stride * count` product above `0x7fffffff`,
  stops with `size`.
- A range whose end offset exceeds the registered buffer length stops with `bounds`.
- A nonzero-size range with a null data pointer stops with `null_data`.
- An attribute count above 32, or a positive attribute count with a null attribute base,
  stops with `attrib`.
- All range and attribute checks happen before bytes are copied, so a stop leaves the
  `MTLBuffer` and the master flag unchanged. The replacement never allocates, replaces, or
  frees a buffer.

## Evidence classes

Observed machine instructions:

- The entry, 70-instruction body, `RET` at `0x15ebf40`, and eight-byte prefix above come
  from the sealed static export. The reads, writes, branches, array strides, running offset,
  and the three indirect callsites are instruction-level observations with the RVAs cited
  above.
- The caller's extra `R9D` and stack argument are observed instructions at `0x14d4d3c` and
  `0x14d4d30`, but the callee body never reads them.

Ghidra hypotheses:

- Ghidra's decompiler recovers the signature
  `void _GfxUpdateVertexBufferMultiple(long *param_1, int *param_2, long param_3,
  long param_4, uint param_5)` and the C-like loop shown above. That is a recovery, not a
  separate proof.
- Ghidra's symbols `___glewBindBufferARB`, `___glewBufferSubDataARB`, and
  `___glewDisableVertexAttribArrayARB`, and the `glBindBufferARB`/`glBufferSubDataARB`/
  `glDisableVertexAttribArrayARB` target guesses, are hypotheses from named, uninitialized
  slots. The static image cannot show the pointers installed after GLEW initialization.

Deliberate Metal safety policy:

- The `thread`, `missing`, `released`, `master`, `range`, `size`, `bounds`, `null_data`, and
  `attrib` stops are safety choices for this backend, not recovered original behavior.
- The decision to validate every range before copying, to keep the same `MTLBuffer`, and to
  treat the attribute loop as deferred state are deliberate Metal policy. The deferred-state
  consumer named above is the later draw/encoder path.

Unresolved assumptions:

- `[header+8]` is only observed as a nonzero guard and as the bind argument. Calling it a GL
  name follows Ghidra's symbol for the bind slot; this backend also uses it as a presence
  token.
- Header `+0` is observed as a stride and header `+4` is not read here. Their create-time
  identities come from the surrounding backend, not from this body.
- The shader object at `[master+0x70]` and its `+0x10`/`+0x18` fields are only known by the
  accesses cited above. The rest of the shader record is not established by this function.
- Whether a later draw consumes the recorded attribute indices themselves, or only the
  `[master+0x170]` byte, is not settled by this function.
- The caller's extra `R9D` and stack argument have no observed use in this body; their
  source-level meaning is unknown.
