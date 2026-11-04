# `_GfxSetScissorRectangle` research note

Resolved symbol: `_GfxSetScissorRectangle` (exact match, internal, in the pinned renderer snapshot).
Entry address (RVA): `0x15ed0aa`. Image base is `0x100000000`, so the runtime address is
`0x1015ed0aa`. Executable sha256:
`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

Everything below comes from `reverse_engineering.research` and
`reverse_engineering.ghidra_query` against `EU4_RE_EXPORT`. No EU4 launch, corpus copy, project
import, or reanalysis was performed for this note.

## Observed instructions

Static body: `[0x15ed0aa, 0x15ed0e2)` half-open. The last instruction starts at `0x15ed0dd`
and is a 5-byte tail `JMP`. 18 instructions, 56 decoded bytes. There is one body range and no
thunk flag.

```
0x15ed0aa  55                    PUSH RBP
0x15ed0ab  4889e5                MOV RBP,RSP
0x15ed0ae  0fbf5604              MOVSX EDX, word ptr [RSI + 0x4]
0x15ed0b2  0fbf06                MOVSX EAX, word ptr [RSI]
0x15ed0b5  4531c0                XOR R8D,R8D
0x15ed0b8  29c2                  SUB EDX,EAX
0x15ed0ba  410f48d0              CMOVS EDX,R8D
0x15ed0be  4c8b0f                MOV R9, qword ptr [RDI]
0x15ed0c1  0fbf7e06              MOVSX EDI, word ptr [RSI + 0x6]
0x15ed0c5  0fbf7602              MOVSX ESI, word ptr [RSI + 0x2]
0x15ed0c9  89f9                  MOV ECX,EDI
0x15ed0cb  29f1                  SUB ECX,ESI
0x15ed0cd  410f48c8              CMOVS ECX,R8D
0x15ed0d1  418bb1ac000000        MOV ESI, dword ptr [R9 + 0xac]
0x15ed0d8  29fe                  SUB ESI,EDI
0x15ed0da  89c7                  MOV EDI,EAX
0x15ed0dc  5d                    POP RBP
0x15ed0dd  e990552d00            JMP 0x1018c2672   ; _glScissor
```

The single outgoing edge is a tail jump to `_glScissor` at `0x18c2672`; there is no `CALL`
and no `RET` in the body. The `JMP` target is an external GL thunk. The two direct callers are
`RenderGfx2dList` (`0x149d5c2`, call at `0x149d6e8`) and `Render` (`0x14ae40a`, call at
`0x14ae4bb`).

### Patchable prefix

The first eight original bytes are

```
55 48 89 e5 0f bf 56 04
```

They cover `PUSH RBP` (1), `MOV RBP,RSP` (3), and `MOVSX EDX,word ptr [RSI+4]` (4), so the
prefix ends exactly on the instruction boundary at `0x15ed0b2`. The registry prefix must be
exactly these bytes. The full replaced body is `[0x15ed0aa, 0x15ed0e2)`.

### Calling convention

SysV x86-64. `RDI` is the immediate-context handle, `RSI` is a pointer to the rectangle. The
handle's first qword is the state/master object: `_GfxGetImmediateContext` (`0x15ea6c2`) is a
five-instruction identity function that returns `RDI` unchanged, and this body immediately
does `MOV R9,[RDI]`. All argument registers are caller-saved and the body touches no
callee-saved register except the pushed `RBP`, which is popped before the tail jump. Return
type is `void`; the tail jump forwards `glScissor`'s return directly to the original caller.
`_GfxSetScissorRectangle(void *handle, const int16_t rect[4])` is the faithful signature.

### Rectangle and coordinate convention

The rectangle is four native-endian `int16_t` values, eight bytes total:

| offset | field | meaning |
|---|---|---|
| `+0x0` | `x1` | left edge |
| `+0x2` | `y1` | top edge |
| `+0x4` | `x2` | right edge |
| `+0x6` | `y2` | bottom edge |

Every field is read with `MOVSX`, so values are signed 16-bit. The rectangle is in
top-left-origin pixel coordinates. This is established by the caller, not only by the
conversion: `RenderGfx2dList` loads the packed qword at `CGraphicalObject+0x118`, clamps
`x1 >= 0`, `y1 >= 0`, `x2 <= viewport_width`, `y2 <= viewport_height`, and enters the scissor
path only when `x1 < x2 && y1 < y2`. The target then computes:

```
width  = x2 - x1        (32-bit; negative -> 0)
height = y2 - y1        (32-bit; negative -> 0)
x      = x1
y      = [master+0xac] - y2
```

`master+0xac` is the viewport height: `_GfxSetViewport` (`0x15ea4f8`) copies the first 16
bytes of its rectangle to `master+0xac`, and its `glViewport(x, y, width, height)` call uses
`width` from `+0xb0` and `height` from `+0xac`. So `y = viewport_height - y2` converts the
top-left-origin bottom edge to GL's bottom-left-origin lower edge. The target does not clamp
`x1`, `y1`, or the computed `x`/`y`; only negative spans become zero. Arithmetic is 32-bit,
so the faithful replacement must reproduce 32-bit wrapping rather than widening to 64-bit.

### Memory effects

Reads only:

- qword at `[handle]` (state/master pointer),
- four signed 16-bit fields at `[rect+0]`, `[+2]`, `[+4]`, `[+6]`,
- dword viewport height at `[master+0xac]`.

Writes: none. No stack frame beyond `PUSH RBP`/`POP RBP`; no callee-saved register is
modified; no global or engine object is touched by this function.

### OpenGL effect

Exactly one state change: `glScissor(x1, viewport_height - y2, max(x2-x1, 0),
max(y2-y1, 0))`. It sets the current GL scissor box. It does not enable or disable
`GL_SCISSOR_TEST`, does not clear, does not draw, and does not flush. The GL error behaviour
of negative values is not reachable because the target clamps both spans to zero, but a zero
span is still passed through to GL unchanged.

## Ghidra hypotheses (not observed instructions)

- Ghidra's decompiler names the first parameter `param_1` and the second `short *param_2` and
  describes the body as `_glScissor((int)*param_2, *(int *)(*param_1 + 0xac) - (int)param_2[3],
  iVar2, iVar1)`. That agrees with the instruction-level reading above.
- Ghidra labels the caller `RenderGfx2dList` as `__thiscall` with `this` and a `CArray *`; that
  is caller-side metadata. It does not change this function's register-level contract.
- The identity `_GfxGetImmediateContext` is inferred to return an object whose first qword is
  the master/state because this body dereferences `[RDI]` before touching `+0xac`. That is a
  hypothesis about the type, not an observed write.
- The rectangle field order and top-left origin are inferred from the caller's clamps and the
  `viewport_height - y2` conversion. No runtime sample was captured for this note.

## Deliberate Metal safety policy

- The replacement must not call GL and must not pretend a GL scissor happened. There is no
  render encoder in this campaign, so the effect is deferred.
- The replacement stores the equivalent top-left-origin Metal rectangle
  `(x1, y1, width, height)` in the backend's pending-scissor state. The original's GL
  `y = viewport_height - y2` is *not* what Metal consumes: `MTLScissorRect` is already
  top-left-origin, so `y = y1` and `height = y2 - y1`.
- The encoder-facing consumer is `-[MTLRenderCommandEncoder setScissorRect:]`. A later render
  pass must read the pending state and call `setScissorRect:MTLScissorRectMake(x, y, width,
  height)` before its draws, and again after any subsequent scissor change. The pending state
  must also be treated as inherited render-pass state, not as a one-shot command.
- `NULL` handle, `NULL` rectangle, a null `[handle]`, or a master with no published backend is
  a no-op. The original would fault on those pointers; refusing to crash is a deliberate
  safety policy for the Metal host, not a recovered behaviour.
- Negative spans clamp to zero exactly like the original. Negative `x1`/`y1` are preserved;
  the replacement does not silently clamp them to the render target.
- The deferred state is not a completed Metal scissor operation. Tests must show the
  transition from unset to set and from one rectangle to another, and must feed the stored
  rectangle to a real `MTLRenderCommandEncoder` to prove the consumer shape.
- Do not add a render encoder, render pass, or draw path for this campaign. If a faithful
  *applied* effect becomes required, that is a separate dependency.

## Unresolved assumptions

- The four `int16_t` fields are assumed to be little-endian and naturally aligned at
  `rect+0`, `+2`, `+4`, `+6`. The binary is x86-64 little-endian, so this is safe in the
  replacement but is not part of the instruction bytes.
- The viewport height at `master+0xac` is assumed to be the same object that the engine
  passes as `[handle]` at render time. `_GfxSetViewport` writes `master+0xac`, and
  `_GfxClear` reads the same field for its own `glScissor`, so this is strongly supported but
  not runtime-confirmed.
- The exact lifetime of the deferred state across an engine reset is not known. This note
  keeps it in the Metal backend and does not attempt to mirror any GL context-loss behaviour.
- The two callers are the only direct ones; indirect or tail-call entry is not required by the
  recovered body.
