# Cross-parent buffer-bind static RE (PR A)

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)  
**Scope:** `CPdxMeshObject::RenderBuckets` mesh subrecord path (`0x14c7da4` … `GfxDrawIndexed` @ `0x14c8404`)

## Hook site → one draw

On the **eligible subrecord path** (opaque/transparent gate at `0x14c8181` satisfied, mesh-type record selected):

1. Site hook @ `0x14c81e6` runs inside the per-subrecord loop **before** `GetGfxEffect` @ `0x14c81f3`.
2. No `continue`/early exit between `0x14c81e6` and `0x14c8404` on that path (static control-flow scan `0x14c81e6`–`0x14c8404`).
3. **One** `GfxDrawIndexed` @ `0x14c8404` per loop iteration ([`render-buckets.md`](render-buckets.md)).

**Terminology:** each `site_entry` on this path corresponds to **one eventual draw** for that subrecord iteration.

## `buffer_bind_signature` (minimum state)

Derived from subrecord fields consumed at buffer helpers ([`mesh-draw-subrecord-0xe8-layout.json`](mesh-draw-subrecord-0xe8-layout.json)):

| Field | Offset | Role |
|-------|--------|------|
| VBO 0 | `+0x30` | First vertex buffer handle |
| IBO | `+0x38` | Index buffer handle |
| VBO 1 | `+0x40` | Second vertex buffer slot |

**`+0x7d` (opaque/transparent gate byte) is not part of buffer-helper semantic identity.** It participates in layer/flush selection before the site hook; the invocation path already fixes flush-array kind. v1 same-parent predicate compared parent equal-fields including gate-related layout; **cross-parent structural/safety signatures use the three handles only** unless future RE proves additional fields are read inside `GfxSetVertexBuffers` / `GfxSetIndexBuffer` callees.

## Helper semantic idempotence (summary)

### `GfxSetVertexBuffers` (@ `~0x15ebf41`)

- Binds vertex buffer(s) from subrecord/mesh inputs on the draw path.
- **Must document** (full objdump on pinned binary): explicit args, pointer-chasing, graphics-context writes, GL `glBindBuffer` / attrib setup side effects, cache dirty flags.
- **Idempotence claim:** repeating with identical complete input state (including same buffer object contents if helpers read metadata) reproduces the same engine buffer-bind state.

### `GfxSetIndexBuffer` (@ `0x15ec28e`)

- Binds element-array buffer; copies IBO first `uint32_t` to deferred context `+0x68` ([`render-buckets.md`](render-buckets.md)).
- Mesh path passes draw count **zero** to helper; actual count consumed from context at `GfxDrawIndexed`.
- **Idempotence claim:** same IBO pointer + same first-field → same context `+0x68` and same GL element binding.

**PR B safety-qualified cross-parent elision requires PR A GO on idempotence for the full signature above.**

## Post-helper state continuity (F2 full interval)

**Question:** After record **A**'s buffer helpers return, does any work until record **B**'s buffer-helper entry change the buffer state represented by `buffer_bind_signature`?

```text
A: GfxSetVertexBuffers  (~0x14c832a)
A: GfxSetIndexBuffer    (~0x14c833b)
        ↓
A: six-slot texture loop (~0x14c834e–0x14c83b1)
A: object constants      (~0x14c83b3–0x14c83f6)
A: GfxDrawIndexed        (0x14c8404)
        ↓
child-loop exit / parent-loop advance
        ↓
B: GetGfxEffect          (0x14c81f3)
B: effect RS/DS/blend/shader if effect changed (~0x14c8204–0x14c8247)
B: texture resolve + GfxSetTextures (~0x14c8274–0x14c82dd)
        ↓
B: buffer-helper entry   (~0x14c832a)
```

### Static assessment (pinned disassembly map)

| Segment | Buffer-bind risk | Notes |
|---------|------------------|-------|
| A post-bind texture loop | **Low direct** | `GfxSetTextures` / slot loop — must not rebind VBO/IBO; verify no hidden buffer binds in callees |
| A object constants | **Low direct** | Updates constant buffer, not VBO/IBO handles in layout |
| A `GfxDrawIndexed` | **Review required** | Performs pending attrib/uniform upload; may touch **vertex attrib binding state** while element buffer often unchanged — **can break “same signature ⇒ skip B helpers”** if draw path rebinding invalidates assumed state |
| Parent transition | **Medium** | No buffer helpers in gap; verify no GL in loop increment/record selection |
| B effect setup | **Medium** | Shader/state change — generally not VBO/IBO, but verify |
| B `GfxSetTextures` | **Medium** | Texture binds only if proven |

### PR A continuity verdict

| Claim | Status |
|-------|--------|
| Structural cross-parent **signature recurrence** observable | **GO** (pending dynamic rates) |
| Safety-qualified **skip B buffer helpers** when signature matches A | **NO-GO pending** — `GfxDrawIndexed` attrib path and B pre-buffer texture/effect work **not proven** to preserve element/vertex buffer bindings across the full interval |

**Follow-up:** complete callee-level objdump for `GfxDrawIndexed` and `GfxSetTextures` on pinned binary before setting `safety_qualified` bit for cross-parent buffer elision.

## Parent transition (explicit)

Prove no VBO/IBO mutation on:

```text
last draw of parent A → exit 0xe8 inner loop → advance 80-byte parent iterator
→ load parent B → enter subrecord loop → reach B setup before 0x14c832a
```

Static map shows buffer binds occur **inside** the per-subrecord setup block, not in parent iteration glue ([`mesh-draw-dependency-map.md`](mesh-draw-dependency-map.md)).

## Invocation boundary

Do **not** use `%rbp` as invocation identity (stack frame reuse across calls). Use **RenderBuckets entry hook** + TLS epoch ([`renderbuckets-entry-hook-site.json`](renderbuckets-entry-hook-site.json)): increment once per function entry before any path reaches `0x14c81e6`.

**Recursion:** none observed on mesh `RenderBuckets` path; shadow pass uses distinct symbol.

## Texture structural inputs (PR A)

Three texture IDs resolved before `GfxSetTextures` (~`0x14c8274`). Structural hypothesis **`same_texture_input_signature`**: compare resolved ID tuple (and slot loop inputs if mapped) — **GO for observer counter** in PR B; safety-qualified texture elision remains **unsupported** until barrier fields resolved in layout JSON.
