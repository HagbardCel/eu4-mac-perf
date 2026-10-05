# Border loop-head prefix classifier

Spec: [border-loop-head-classifier.json](border-loop-head-classifier.json)  
Implementation: [tools/border_loop_classifier.py](../tools/border_loop_classifier.py)

## API

```python
classify_batchable_prefix(
    context,
    record_table,
    ibo_table,
    side_entries,          # walk order
    *,
    ibo_bind_topology="ONE_BIND",
    max_scan_steps=None,
    walk_exhausted=True,
) -> PrefixResult
```

## Eligibility

```text
batch_eligible =
    run_length >= 2
    AND entry_state_matches_batch_key (color/vbo; ibo only for ZERO_BIND)
    AND included steps homogeneous
```

Boundary at step k **does not** invalidate prefix `0..k-1`.

## Hook pending flags (fall through one draw)

| Field | Predicate |
|-------|-----------|
| `deferred_attrib_upload_pending` | `+0x128` on deferred context inner object |
| `secondary_upload_pending` | `+0x170` on same object (GfxDrawIndexed secondary upload) |
| `outer_batch_index` | `uint32(memory32[-0xe4(%rbp)])`; `special_precolor_outer_batch` when mask `0xB0` bit set and index ≤ 7 (bits **4, 5, 7**) |

## Record `+0x04`

`RecordView.arg3_u16_at_plus_04` mirrors the uint16 loaded into `%edx` at the callsite. **Not** used for batch homogeneity and **not** passed to `glMultiDrawElements` as basevertex (dead in GfxDrawIndexed on mode-0).

## Skip (`%r13b`)

```text
drawable = (skip_or_visibility_mask & visibility_byte_at_plus_1) != 0
```

Evaluate before `record_index` / `RecordView` / IBO dereference.

## IBO (`ONE_BIND` default)

- `BatchKey.ibo_identity` / `BatchKey.ibo_argument` from `ibo_table[record_index]` (same pointer per GOG 1.37.5 RE).
- `ibo_argument == 0` → not batch eligible (GfxSetIndexBuffer no-op).
- Mutation: `GfxSetIndexBuffer(prefix.batch_key.ibo_argument)`.

## `termination_kind`

| Kind | Meaning |
|------|---------|
| `BARRIER` | Stopped at skip/color/vbo/ibo/helper boundary |
| `WALK_END` | Inner walk exhausted (`walk_exhausted=True`) → terminal continuation |
| `SCAN_CAP` | `max_scan_steps` reached; more walk may remain — **not** barrier telemetry |
| `INVALID` | OOB index / fail closed |

## PrefixResult

Includes `boundary_reason_mask`, `eligible_draw_calls_eliminable = max(0, run_length - 1)`.

Barrier telemetry: count **set bits** (non-exclusive).
