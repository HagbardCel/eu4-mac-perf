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

## Skip (`%r13b`)

```text
drawable = (skip_or_visibility_mask & visibility_byte_at_plus_1) != 0
```

Evaluate before `record_index` / `RecordView` / IBO dereference.

## IBO (`ONE_BIND` default)

- `required_ibo_identity` / `required_ibo_argument` from `ibo_table[record_index]` (same value per RE).
- Entry IBO match not required; mutation calls `GfxSetIndexBuffer(argument)` once.

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
