# Gate 3b — continuation state (prefix-batch model)

## Loop tail (reference)

```asm
1010cc3bd  movq -0x70(%rbp), %rcx      ; cursor from stack
1010cc3c1  leaq -0x2(%rcx), %rax
1010cc3c5  addq $4, %rcx               ; advance live rcx
1010cc3c9  addq $4, %rax
1010cc3cd  cmpq -0x198(%rbp), %rax
1010cc3d4  movl -0xe4(%rbp), %ebx
1010cc3da  jne 0x1010cbe55
1010cc3e0  incl %ebx                   ; terminal outer-step entry
```

`-0x70(%rbp)` is written at `0x1010cbe59` on each loop-head entry, not updated in the tail.

## Continuation classes

| Class | `termination_kind` | Resume |
|-------|-------------------|--------|
| Interior | `BARRIER`, `SCAN_CAP` | Displaced `testb`+`movq` + `je` semantics; `%rcx` = first-unconsumed entry |
| Terminal | `WALK_END` | `@ 0x1010cc3e0` after k tail-equivalent steps |

### Terminal exact state (k drawable steps consumed)

Let `cursor_k` = index-table pointer for entry k (first **non-drawn** step). After original tail for entries `0..k-1`:

```text
%rcx          = cursor_k          (value tail would load before compare)
%rax          = cursor_k + 2      (equals -0x198 end bound when WALK_END)
-0x70(%rbp)   = cursor_{k-1}      (last stored at loop head before final tail)
-0x198(%rbp)  = end bound (unchanged)
%ebx          = reloaded from -0xe4(%rbp) @ 0x1010cc3d4
-0x51, -0x64  = prefix batch color/VBO caches
RFLAGS        = DEAD @ 0x1010cc3e0 (no flag consumer before incl)
-0x80(%rbp)   = DEAD at interior trampoline entry (loaded @ 0x1010cbe63 only on draw path)
```

### Interior trampoline

```text
%rcx          = first-unconsumed entry pointer (LIVE)
-0x70(%rbp)   = set by displaced movq (LIVE through je)
RFLAGS        = LIVE (testb + je)
```

## Trampoline / callee-saved discipline

Mutation path is **non-leaf** (calls engine helpers + GL). Trampoline must preserve:

```text
%rbp, %rbx, %r12-%r15     callee-saved — must not clobber
%rsp                      16-byte aligned before each call
```

Do not rely on leaf red-zone for scratch across calls.

### Caller-saved after ONE_BIND batch sequence

Document liveness **after** `GfxSetIndexBuffer` + `glMultiDrawElementsBaseVertex` return, before interior/terminal resume:

| GPR | Interior resume | Terminal resume |
|-----|-----------------|-----------------|
| `%rdi,%rsi,%rdx,%rcx,%r8,%r9,%r10,%r11` | May be clobbered by calls — reload resume state from stack/TLS | Same |
| `%r12` (`%r12` = SBorderDrawInfoSet) | LIVE (unchanged by batch if preserved in trampoline) | LIVE |
| `%r14` record base | LIVE if still in original loop scope | LIVE |

### XMM (fast-color path @ `0x1010cc22d` join)

Slow-color XMM traffic @ `0x1010cc1ef`–`0x1010cc222` is skipped on fast-color prefix. At hook `0x1010cbe55`, **no XMM reload is required before skip test** — XMM **DEAD** for skip-only interior trampoline. After MDEBV/GfxSetIndexBuffer calls, XMM0–7 may be clobbered per SysV; XMM8–15 callee-saved must be preserved by trampoline if still live in `DrawBorders` frame (audit: no XMM use between `0x1010cc22d` and `0x1010cbe55` on fast path).

## GfxSetIndexBuffer @ `0x1010cc2ce` (macOS SysV)

```text
rdi = movq -0x60(%rbp), %rdi   @ 0x1010cc2ca  (GfxDeferredContextGFX*)
rsi = movq (%rax,%r15,8), %rsi @ 0x1010cc2c6  (= batch_key.ibo_argument)
```

No stack arguments. Null `rsi` → helper returns without bind (`testq %rsi` @ `0x1015ec28e`).

## glMultiDrawElementsBaseVertex (mutation TLS arrays)

```text
rdi  = GL_TRIANGLES (0x0004)
rsi  = &count[i]      (3 * triangle_count per record)
rdx  = GL_UNSIGNED_SHORT (0x1403)
rcx  = &indices[i]    (zero offsets — border path)
r8   = drawcount      (prefix run_length)
r9   = &basevertex[i]
```

16-byte stack alignment before `callq`. Engine import via `_glew*` tail same as `GfxDrawIndexed` base-vertex path.

## Engine / GL (`ONE_BIND`)

One `GfxSetIndexBuffer(batch_key.ibo_argument)` then MDEBV replaces N×(bind+GfxDrawIndexed) for homogeneous mode-0 prefix subject to classifier exclusions (null IBO, zero tri, pending `+0x128`, etc.).

## Non-recursive re-entry

14-byte patch @ `0x1010cbe55`; see [border-hook-site.json](border-hook-site.json) incoming-CF audit.
