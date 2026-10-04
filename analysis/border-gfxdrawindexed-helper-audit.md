# Gate 3a — border helper-side-effect audit (`GfxDrawIndexed` + `GfxSetIndexBuffer`)

**Binary:** GOG EU IV 1.37.5 x86-64  
**GfxDrawIndexed:** `0x1015eaa77`  
**GfxSetIndexBuffer:** `0x1015ec28e`  
**Mode-0 border path:** `0x1010cc2c1`–`0x1010cc2e8`

## Production topology (this PR)

```text
ibo_bind_topology: ONE_BIND
```

Mutation batch path: **one** `GfxSetIndexBuffer(required_ibo_argument)` using the same `rsi` pointer loaded at `0x1010cc2c6` (`movq (%rax,%r15,8), %rsi`), then `glMultiDrawElementsBaseVertex` for the prefix.

`ZERO_BIND` (elide all N binds while relying on entry IBO shadow) was rejected for static closure: hook runs **before** per-iteration `GfxSetIndexBuffer`, so shadow observability at re-entry is not proven without a second hook.

## IBO table → helper argument

```asm
1010cc2c1  movq 0x60(%r12), %rax
1010cc2c6  movq (%rax,%r15,8), %rsi    # record_index in %r15
1010cc2ce  callq GfxSetIndexBuffer
```

`required_ibo_identity` and `required_ibo_argument` are the **same pointer value** in `rsi` (classifier models as `int` handle id). Homogeneity compares identity; ONE_BIND passes that pointer once.

### GfxSetIndexBuffer side effects (`0x1015ec28e`)

| Effect | Evidence |
|--------|----------|
| Early out if `rsi==0` | `testq %rsi` @ `0x1015ec28e` |
| GL bind via indirect call | `movl 0x4(%rsi), %esi` + `callq *(%rax)` |
| Deferred context cache | `movl (%rbx), %eax` → `0x68(%r14)` |

**Equivalence adopted:** \(N \times GfxSetIndexBuffer(I) \equiv 1 \times GfxSetIndexBuffer(I)\) before multidraw when prefix IBO is homogeneous (same `rsi`). Skipped intermediate binds must not be required for stats/profiler beyond what the single bind establishes — no additional engine reads of bind counters were found on the border hot path in this audit pass.

## GfxDrawIndexed

| Behavior | Evidence |
|----------|----------|
| `esi<=0` uses context `+0x68` count | `0x1015eaa94`–`0x1015eaa98` |
| Deferred attrib upload | `cmpb $0, 0x128(%r12)` → loop `0x1015eaad3`–`0x1015eab05`, clear `0x128` |
| Base vertex dispatch | `testl %r14d` @ `0x1015eac16`: **0** → `jmp 0x1018c2606` (`glDrawElements`); **≠0** → indirect `glDrawElementsBaseVertex` |
| Border mode-0 args | `ecx=0` @ `0x1010cc2e6`, `esi=3*triangle_count` @ `0x1010cc2d9`–`0x1010cc2e8` |

### Classifier fall-through (conservative)

| Condition | Policy |
|-----------|--------|
| `triangle_count == 0` | Boundary `OTHER_SIDE_EFFECT` — `esi` may be 0 → context-count fallback in helper |
| `deferred_attrib_upload_pending` (`+0x128`) | Fall through one original `GfxDrawIndexed` at hook |

### Base vertex == 0 vs multidraw

Original per-record: `basevertex==0` → `glDrawElements`; `!=0` → `glDrawElementsBaseVertex`. Replacement uses `glMultiDrawElementsBaseVertex` with per-draw `basevertex[i]`. **Conclusion:** Khronos semantics treat `basevertex=0` as valid for MDEBV; no extra classifier barrier for zero basevertex members (border records commonly use small positive basevertex; zero remains supported).

### Draw-call count

Internal draw/stat increments inside helpers are **diagnostic-only** for mutation GO (same rule as GL-tail interpose charter) unless future RE shows game logic consumption.

## Required conclusion (met for ONE_BIND subset)

1. \(N \times GfxDrawIndexed \equiv 1 \times MDEBV\) for eligible mode-0 prefix **excluding** zero-count and pending-attrib hook states, with **one** upfront `GfxSetIndexBuffer` replacing N binds.  
2. Side effects not reproduced: attrib upload when `+0x128` set (classifier fall-through); zero triangle count (boundary).
