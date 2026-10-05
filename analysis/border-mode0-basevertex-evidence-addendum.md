# Mode-0 border basevertex evidence reconciliation (GOG 1.37.5)

**Binary:** `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`  
**Scope:** `CPdxMapBorderLayer::DrawBorders` mode-0 path @ `0x1010cc2e8`

## Verdict: Case **B** (mislabeled engine argument, not observed GL basevertex)

Prior docs treated record `+0x04` as the effective OpenGL base vertex because:

1. Disassembly loads `movzwl 0x4(%r14,%rbx), %edx` before `call GfxDrawIndexed`.
2. Narrative assumed `GfxDrawIndexed` tail-jumps to `glDrawElementsBaseVertex` when that value is nonzero.

Static RE on the pinned binary refutes (2) for mode-0 border:

| Site | Finding |
|------|---------|
| `0x1010cc2e6` | `xorl %ecx, %ecx` — fourth GfxDrawIndexed argument is **0** |
| `0x1015eaa8b` | `movl %ecx, %r14d` — branch selector is **%ecx**, not `%edx` |
| `0x1015eac16` | `testl %r14d` → `glDrawElements` when `%ecx==0` (always on mode-0 border) |
| GfxDrawIndexed body | Incoming **`%edx` is not consumed** on the mode-0 hot path |

Therefore record `+0x04` is **not** `basevertex[i]` for the realized GL draw on mode-0.

## Trace / frame-model provenance

| Source | What `base_vertex` actually means | Border mode-0 implication |
|--------|-----------------------------------|-------------------------|
| [eu4_draw_trace.c](../benchmark/eu4_draw_trace.c) `capture()` | GL interpose: `api=1` → `base=0`; `api=2` → `glDrawElementsBaseVertex` arg | Border draws that reach **`glDrawElements`** record **`base_vertex=0`** |
| [eu4_frame_model.py](../benchmark/eu4_frame_model.py) `D` rows | Same signed GLint from observer wire format | Held-out border projection samples show **`base_vertex: 0`** |
| [mesh-render-path.md](mesh-render-path.md) (pre-correction) | Conflated record `+0x04` with GL basevertex | **Superseded** by this addendum |

The historical “2,285 **distinct** base vertices” line counted **per-record `+0x04` variation**, not 2,285 distinct GL `base_vertex` values from interposed draws. Draw counts (~2,285/frame) and same-state adjacency screening remain valid; **MDEBV solely because base vertices vary is rejected**.

## Authoritative field semantics (mode-0)

```text
+0x04  uint16 → loaded into %edx at callsite; DEAD inside GfxDrawIndexed on this path
+0x06  uint16 triangle count → esi = 3 * field → GfxDrawIndexed index count
indices GL argument: 0 (element-array offset) with bound IBO
effective GL: glDrawElements(GL_TRIANGLES, count, GL_UNSIGNED_SHORT, 0)
```

## Multidraw replacement

Chosen mutation API: **`glMultiDrawElements`** with `indices[i]=0` for all subdraws.  
`glMultiDrawElementsBaseVertex` with all-zero basevertex is equivalent but unnecessarily strong; **do not** populate basevertex from `+0x04`.

## Superseded statements (do not cite without this addendum)

- [draw-path-decision.md](draw-path-decision.md) — “varying base vertices” → varying **record `+0x04`**, not GL basevertex on mode-0.
- [border-multidraw-re.md](border-multidraw-re.md) — MDEBV rationale from distinct basevertex.
- [border-draw-record-layout.json](border-draw-record-layout.json) — `api_parameter: basevertex[i]` at `+0x04` — reclassified.
