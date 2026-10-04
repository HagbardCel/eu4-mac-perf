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

Let `last_cursor` = index-table pointer for entry `k-1` (last **drawn** step). After original tail for entries `0..k-1`:

```text
%rcx          = last_cursor + 4
%rax          = last_cursor + 2
```

At `WALK_END` (walk exhausted, `last_cursor + 2 == end_bound`):

```text
%rax          = end_bound
%rcx          = end_bound + 2
-0x70(%rbp)   = last_cursor (last stored at loop head before final tail)
-0x198(%rbp)  = end bound (unchanged)
%ebx          = reloaded from -0xe4(%rbp) @ 0x1010cc3d4
-0x51, -0x64  = prefix batch color/VBO caches
RFLAGS        = DEAD @ 0x1010cc3e0 (no flag consumer before incl)
-0x80(%rbp)   = DEAD at interior trampoline entry (loaded @ 0x1010cbe63 only on draw path)
```

### `%rax` / `%rcx` at `0x1010cc3e0` (preferred)

After `jne` not taken, execution reaches `incl %ebx` with **no intervening use** of `%rax` or `%rcx`. Mutation **need not synthesize** `%rax`/`%rcx` for terminal resume — only restore live `%ebx` path and stack locals the outer step expects.

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

### macOS x86-64 SysV — caller-saved after inserted calls

**XMM0–XMM15** and **`%rax`** are **caller-saved**. `GfxSetIndexBuffer` and `glMultiDrawElements` may clobber all XMM registers and `%rax`.

| Register class | At interior resume after batch | At terminal `0x1010cc3e0` |
|----------------|-------------------------------|---------------------------|
| `%rdi`–`%r11`, `%r10`, `%rax` | Reload resume state from stack; do not assume preserved | Same |
| `%r12` (`SBorderDrawInfoSet`) | LIVE if trampoline preserves callee-saved set | LIVE |
| `%r14` record base | LIVE in loop scope | LIVE |
| XMM0–15 | **DEAD** on fast-color path from `0x1010cc22d`–`0x1010cbe55` (no XMM traffic); calls clobber — treat as DEAD unless proven live and saved | Same |

Slow-color XMM traffic @ `0x1010cc1ef`–`0x1010cc222` is skipped on fast-color prefix. At hook `0x1010cbe55`, XMM **DEAD** for skip-only interior trampoline.

## GfxSetIndexBuffer @ `0x1010cc2ce` (macOS SysV)

```text
rdi = movq -0x60(%rbp), %rdi   @ 0x1010cc2ca  (GfxDeferredContextGFX*)
rsi = movq (%rax,%r15,8), %rsi @ 0x1010cc2c6  (= batch_key.ibo_argument)
```

No stack arguments. Null `rsi` → helper returns without bind (`testq %rsi` @ `0x1015ec28e`).

## glMultiDrawElements (mutation TLS arrays)

```text
rdi  = GL_TRIANGLES (0x0004)
rsi  = &count[i]      (3 * triangle_count per record)
rdx  = GL_UNSIGNED_SHORT (0x1403)
rcx  = &indices[i]    (zero offsets — border mode-0)
r8   = drawcount      (prefix run_length)
```

Five-register SysV call (no `r9` basevertex array). 16-byte stack alignment before `callq`. Resolve via engine `_glew*` / GL dispatch same as existing indexed draw path.

`glMultiDrawElementsBaseVertex` with all `basevertex[i]=0` is rendering-equivalent but **not** chosen — see [border-mode0-basevertex-evidence-addendum.md](border-mode0-basevertex-evidence-addendum.md).

## Engine / GL (`ONE_BIND`)

One `GfxSetIndexBuffer(batch_key.ibo_argument)` then **`glMultiDrawElements`** replaces N×(bind+GfxDrawIndexed) for homogeneous mode-0 prefix subject to classifier exclusions (null IBO, zero tri, pending `+0x128` / `+0x170`, etc.).

## Non-recursive re-entry

14-byte patch @ `0x1010cbe55`; see [border-hook-site.json](border-hook-site.json) incoming-CF audit.
