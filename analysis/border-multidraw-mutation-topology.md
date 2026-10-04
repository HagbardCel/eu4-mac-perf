# Border multidraw mutation topology

## Architecture choice: **run-head / draw-tail interpose** (PR C)

| Option | Verdict |
|--------|---------|
| Loop-head binary patch @ `0x1010cbe55` | **Preferred long-term** — inspect `r15` run length and `-0x198(%rbp)` end before first draw |
| Run-head gather + skip loop | Requires synthetic post-loop state (3b) — feasible but deferred to loop-head patch v2 |
| Leaf `GfxDrawIndexed` patch (3 sites) | Fragile instruction sizing |
| **PR C implementation** | **`glDrawElementsBaseVertex` interpose** — return addresses `0x10cc2ed`, `0x10cc357`, `0x10cc3bd` identify border draws; fail-closed batch flush on signature change |

Gate 2 (realizability): **PASS** for interpose path — complete batch known at flush boundary when next draw would change tracked signature.

## Gate 3a — temporal equivalence

**PASS (mode-0 fast-path subset):** Batching only when consecutive interposed draws share site id 0 signature `(return RA bucket, index count scaling, IBO bind generation)`. Color/VBO slow paths force flush via fallback counters.

**FAIL (unconditional whole-function):** Cannot replace entire `DrawBorders` without reproducing color/setup branches — not attempted.

## Gate 3b — post-loop state equivalence

**N/A for PR C interpose** (no loop skip). Loop-head patch must restore `%rcx` walk pointer, `-0x70(%rbp)`, `-0xe4(%rbp)`, and `ebx` at `0x1010cc3bd` / `0x1010cc3e0`.

Documented synthetic exit for future loop-head:

```text
; after multidraw covering N records from current index:
addq $N, %r15
lea  28*N(%r14), %r14   ; if needed
mov  updated, -0x80(%rbp)
jmp  0x1010cc3bd
```

## Argument marshalling

| Question | Answer |
|----------|--------|
| Pre-built arrays? | **No** — gather from interposed draw parameters |
| Scratch | TLS `GLsizei counts[EU4_BORDER_MAX_BATCH]` (1024), `GLint basevertex[]`, `const void *indices[]` all **NULL offset** |
| Heap in loop | **Forbidden** |
| Max batch | 1024 subdraws (clamp; longest observed ~943) |
| `indices[i]` | Constant **0** |
| Gather cost | O(N) per batch, one multidraw — **negligible** vs N driver calls |

## Hard realizability verdict

**GO for PR C prototype** on interpose + mode-0 batching with fail-closed fallback. Loop-head patch remains follow-up for net deployable win without per-draw interpose overhead.
