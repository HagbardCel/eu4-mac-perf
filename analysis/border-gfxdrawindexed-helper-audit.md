# Gate 3a — border helper-side-effect audit (`GfxDrawIndexed` + `GfxSetIndexBuffer`)

**Binary:** GOG EU IV 1.37.5 (`b3d38876…`)  
**Topology:** `ONE_BIND` — one `GfxSetIndexBuffer(batch_key.ibo_argument)` then MDEBV per eligible prefix.

## GfxSetIndexBuffer (`0x1015ec28e`)

| Check | Finding |
|-------|---------|
| Null `rsi` | `testq %rsi; je ret` @ `0x1015ec291` — **no GL bind, no `0x68` write** |
| GL dispatch | Indirect call with `edi=0x8893`, index from `0x4(%rsi)` |
| Deferred cache write | `movl (%rbx), %eax` → `0x68(%r14)` where `r14 = *(rdi)` |
| Draw/stat counter in helper body | **Not found** in `0x1015ec28e`–`0x1015ec2bb` (25 insn window) |
| Profiler | **Not found** in same window |

**Classifier:** `ibo_argument == 0` → `OTHER_SIDE_EFFECT` (ONE_BIND cannot establish IBO).

## GfxDrawIndexed (`0x1015eaa77`)

| Offset / behavior | R/W | Border relevance |
|-------------------|-----|------------------|
| `esi<=0` → `0x68(%r12)` count | R | Zero `triangle_count` → boundary |
| `+0x128` byte | R/C | Attrib upload loop `0x1015eaad3`–`0x1015eab05`; cleared after |
| `+0x170` byte | R/C | Conditional block `0x1015eabe5`–`0x1015eac0d` (secondary upload path) |
| `+0x70` deferred attrib table | R | Upload loop |
| `r14d==0` vs `!=0` | — | `glDrawElements` @ `0x1015eac4a` vs `glDrawElementsBaseVertex` tail @ `0x1015eac48` |

### Audit categories (scoped search)

| Category | Scope | Result |
|----------|-------|--------|
| Draw/stat counters | Full `GfxDrawIndexed` through both GL tails (`0x1015eaa77`–`0x1015eac6a`) | **No** `inc`/`add` to global stats in this function body |
| Profiler markers | Same | **Not found** |
| Debug/error | `0x1015eab1e` path when attrib parent null | **Irrelevant** on border mode-0 hot path (attrib parent live when `+0x128` set) |
| Cached state beyond GL | `+0x68`, `+0x128`, `+0x170` as above | Documented; MDEBV must not skip required attrib flush when `+0x128` set at hook |

### Base vertex == 0

Border may use `ecx=0` @ `0x1010cc2e6` → `glDrawElements` in helper. MDEBV with `basevertex[i]==0` is **equivalent** for rendering (Khronos); no extra classifier barrier.

## Required conclusion (ONE_BIND eligible subset)

1. \(N \times (GfxSetIndexBuffer + GfxDrawIndexed) \equiv 1 \times GfxSetIndexBuffer + 1 \times MDEBV\) for mode-0 homogeneous prefixes **excluding**: null `ibo_argument`, `triangle_count==0`, `deferred_attrib_upload_pending` at hook, unsupported mode, `INVALID` classifier termination.
2. Side effects not reproduced without fall-through: `+0x128` attrib upload when pending at hook.
