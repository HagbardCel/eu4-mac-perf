# Gate 3a — `GfxDrawIndexed` helper-side-effect audit

**Symbol:** `_GfxDrawIndexed` @ `0x1015eaa77`  
**Border callsites:** `0x1010cc2e8`, `0x1010cc352`, `0x1010cc3b8`

## Transformation under review

```text
Original:  setup; GfxDrawIndexed; setup; GfxDrawIndexed; ...
Replacement: glMultiDrawElementsBaseVertex (loop-head prefix)
```

Proving loop setup removable is **insufficient** — must audit the helper itself.

## Known (from static inspection + border path)

| Behavior | Evidence |
|----------|----------|
| Uses `GL_UNSIGNED_SHORT`, index offset 0 on border path | [mesh-render-path.md](mesh-render-path.md) |
| Tail-jump to `glDrawElementsBaseVertex` when base vertex ≠ 0 | same |
| Reads draw count from context when caller passes `esi==0` | `GfxDrawIndexed` @ `0x1015eaa98` (`movl 0x68(%r12), %r15d`) — border passes non-zero `esi` from `3*tri` @ `0x1010cc2d9` |
| Uploads vertex attribs from deferred context before GL dispatch | `0x1015eaacc` loop over attrib slots (`rbx` 0..0x18`, `callq *(%r13)`) |
| Clears deferred upload flag on context | `movb $0, 0x128(%r12)` @ `0x1015eab07` after attrib loop |

## Entry structure (GOG 1.37.5 otool)

```text
1015eaa77  GfxDrawIndexed:
  rdi = GfxDeferredContextGFX*; r12 = *rdi
  r14d = 4th arg (ecx); r15d = index count (esi) or context+0x68 if esi<=0
  1015eaaa7  if context+0x128: attrib upload loop 1015eaad3..1015eab05
  ... early-out / error paths at 1015eab1e+
```

Border mode-0 always passes **esi = 3×triangle_count** and **ecx = 0** (`0x1010cc2e6`–`0x1010cc2e8`), so the `esi<=0` context fallback is not used on this path.

## Audit checklist (open / partial)

| Category | Status |
|----------|--------|
| Argument normalization | Partial — border mode-0 passes explicit count/basevertex |
| GL dispatch choice | Known — base vertex path |
| Draw/stat counters | **Open** — distinguish diagnostic-only `drawCalls++` from logic-consuming counters |
| Profiler markers | **Open** |
| Debug/error handling | **Open** |
| Cached engine state beyond GL | **Open** — attrib upload loop @ `0x1015eaacc` |

**Rule:** rendering/state semantics vs intentionally changed **draw-call-count** semantics. An internal counter is harmless only if proven **diagnostic-only** (not read by game logic or later rendering decisions).

## Required conclusion

One of:

1. \(N \times \text{GfxDrawIndexed} \equiv 1 \times \text{glMultiDrawElementsBaseVertex}\) for the eligible subset, or  
2. List side effects that must be **reproduced** outside multidraw.

**Verdict (this PR):** audit **incomplete** — attrib upload + counter/profiler paths need callee-level completion before mutation Static GO can be unconditional.
