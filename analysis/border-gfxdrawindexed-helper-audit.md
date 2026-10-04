# Gate 3a — border helper-side-effect audit (`GfxDrawIndexed` + `GfxSetIndexBuffer`)

**Binary:** GOG EU IV 1.37.5 (`b3d38876…`)  
**Topology:** `ONE_BIND` — one `GfxSetIndexBuffer(batch_key.ibo_argument)` then **`glMultiDrawElements`** per eligible prefix.

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

### Mode-0 border callsite register map (`0x1010cc2d3`–`0x1010cc2e8`)

| Arg | Source | GfxDrawIndexed use |
|-----|--------|-------------------|
| Index count | `esi` from `3 * record[+0x06]` | Consumed |
| `%edx` | `movzwl record[+0x04]` | **Not read** in helper body on pinned binary |
| `%ecx` | `xor %ecx,%ecx` @ `0x1010cc2e6` | `movl %ecx,%r14d` @ `0x1015eaa8b`; `test %r14d` → **`glDrawElements`** when zero |

See [border-mode0-basevertex-evidence-addendum.md](border-mode0-basevertex-evidence-addendum.md). **Do not** barrier on nonzero `+0x04`.

| Offset / behavior | R/W | Border relevance |
|-------------------|-----|------------------|
| `esi<=0` → `0x68(%r12)` count | R | Zero `triangle_count` → boundary |
| `+0x128` byte | R/C | Attrib upload loop `0x1015eaad3`–`0x1015eab05`; cleared after |
| `+0x170` byte | R/C | `cmpb 0x170(%r12)` @ `0x1015eabe5`; may `call 0x1015ea970`; cleared on path |
| `+0x70` deferred attrib table | R | Upload loop |
| `r14d==0` vs `!=0` | — | `glDrawElements` @ `0x1015eac4a` vs `glDrawElementsBaseVertex` tail @ `0x1015eac48` |

### `+0x170` resolution (option **D**)

| Option | Treatment |
|--------|-----------|
| A/B/C | Rejected for v1 — independent of `+0x128`; not modeled as silent batch |
| **D** | `secondary_upload_pending` @ hook: `cmpb $0, 0x170(%rX)` on `*(GfxDeferredContextGFX*)` inner object (same chain as `+0x128`) → **fall through one original draw** |

**Skipped-path writer proof (mode-0 eligible span):** From hook `0x1010cbe55` through mode-0 setup to `call GfxDrawIndexed` @ `0x1010cc2e8`, disassembly shows only index-table cursor work, color/VBO cache compares, `GfxSetVertexBuffers`, `GfxSetIndexBuffer`, and the draw call. No `mov` into `0x170(%r12)` on that span; `+0x170` is **read/cleared inside GfxDrawIndexed** only. Slow-color and VBO-transition records are already classifier barriers and do not participate in batched prefixes.

### Audit categories (scoped search)

| Category | Scope | Result |
|----------|-------|--------|
| Draw/stat counters | Full `GfxDrawIndexed` through both GL tails (`0x1015eaa77`–`0x1015eac6a`) | **No** `inc`/`add` to global stats in this function body |
| Profiler markers | Same | **Not found** |
| Debug/error | `0x1015eab1e` path when attrib parent null | **Irrelevant** on border mode-0 hot path (attrib parent live when `+0x128` set) |
| Cached state beyond GL | `+0x68`, `+0x128`, `+0x170` as above | Classifier fall-through when `+0x128` or `+0x170` pending at hook |

## Required conclusion (ONE_BIND eligible subset)

1. \(N \times (GfxSetIndexBuffer + GfxDrawIndexed) \equiv 1 \times GfxSetIndexBuffer + 1 \times glMultiDrawElements\) for mode-0 homogeneous prefixes **excluding**: null `ibo_argument`, `triangle_count==0`, `deferred_attrib_upload_pending` or `secondary_upload_pending` at hook, unsupported mode, `INVALID` classifier termination.
2. Side effects not reproduced without fall-through: `+0x128` attrib upload and `+0x170` secondary upload when pending at hook.
3. GL gather uses `count[i]=3*triangle_count`, `indices[i]=0`; **no** basevertex array.
