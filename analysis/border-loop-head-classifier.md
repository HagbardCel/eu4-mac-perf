# Border loop-head prefix classifier

Machine-readable spec: [border-loop-head-classifier.json](border-loop-head-classifier.json)  
Reference implementation: [tools/border_loop_classifier.py](../tools/border_loop_classifier.py)

## API

```python
classify_batchable_prefix(context: LoopContext, records: list[RecordView], side_entries: list[IndexTableEntry]) -> PrefixResult
```

## Eligibility

```text
batch_eligible =
    prefix_homogeneous(run_length >= 2)
    AND entry_state_matches_batch_key(record_0)
    AND no blocking barrier on records 0..k-1
```

### `entry_state_matches_batch_key`

Not “state exists” — **current engine state must equal batch_key(record_0)**:

```text
color_matches = (known && cached_color == batch_key.color_state) | unknown
vbo_matches   = (known && cached_vbo   == batch_key.vbo_table_index) | unknown
ibo_matches   = (known && current_ibo  == batch_key.ibo_key) | unknown
```

If any component is `unknown` or `mismatch` → **v1 fall-through**: execute one original record, retry hook on next iteration.

### `batch_key` (homogeneous run identity)

```text
mode, color_state, vbo_table_index, ibo_key, index_type=GL_UNSIGNED_SHORT
```

## Barrier mask (non-exclusive)

| Bit | Meaning |
|-----|---------|
| `SKIP` | `testb` path would skip (v1: batch boundary, no prefix leap) |
| `UNSUPPORTED_MODE` | `mode != 0` |
| `COLOR_TRANSITION` | color byte ≠ run key |
| `VBO_TRANSITION` | `+0x18` table index changes |
| `IBO_TRANSITION` | IBO selector changes |
| `OTHER_SIDE_EFFECT` | uncategorized / unknown entry state |

Telemetry: count **set bits** per barrier event (sums may exceed event count when multiple bits set).

## PrefixResult fields

```text
run_length
stop_reason_mask
batch_key
entry_state_matches_batch_key { color, vbo, ibo }  # match | mismatch | unknown
draws_covered
eligible_draw_calls_eliminable = max(0, run_length - 1)
batch_eligible
v1_fall_through_recommended
```

## Future phase counters

**A and B both record:**

```text
eligible_runs, eligible_draws, eligible_draw_calls_eliminable
```

**B only:**

```text
actual_multidraw_calls, actual_draw_calls_eliminated
```

Invariant: `actual_draw_calls_eliminated <= eligible_draw_calls_eliminable` **within B**.
