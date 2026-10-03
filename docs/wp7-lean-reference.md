# WP7 — lean production REFERENCE

## Status

| PR | Branch | Content |
|----|--------|---------|
| **A** | `wp7-lean-reference` | Lean `MODE_REFERENCE` hot path, `wp7_lean_reference_validity_v1`, native/portable regressions |
| **B** | `wp7-training-requalification` | One Tier-1 training requalification after A merges (no new decomposition ladder) |

**WP6 is closed.** Reconciliation [`a1dfde61`](wp6-reference-cpu-ladder-memo-20261003.md) showed active REFERENCE tax is largely the removable accounting stack; marginal ablations are not planned.

## Problem

Production `MODE_REFERENCE` today runs nested scopes, per-hook clocks, `event()` aggregation, scope-tree snapshot, GL counter paths, and repeated control snapshots — appropriate for PROFILE / forensic modes, not for a **causal control**.

`wp6_minimal_reference_v2` removes that stack but **publishes zero frames**; it is a decomposition stage only.

## Design target

```text
minimal-reference (test)
    + smallest trustworthy published frame record
    = production lean REFERENCE (WP7)
```

REFERENCE is a **causal control**, not a detailed profiler mode. PROFILE, counters, and forensic paths stay unchanged.

### Lean update envelope (implemented)

```text
snapshot control / epoch identity
one start wall + CPU clock
real_update()  →  render/present pass-through (no REFERENCE scopes/events)
one end wall + CPU clock
publish via `publish_lean_reference_frame()` (`LeanReferenceFrame` → writer expands to CSV `F` with zeros for unused fields)
```

The measured thread does **not** zero/copy the full ~8 KiB legacy `Frame`. `hooked_flush()` pass-throughs in lean REFERENCE before present accounting clocks.

### Removed from REFERENCE (not from PROFILE)

- Nested LOOP / UPDATE / RENDER / PRESENT scopes and scope clocks
- `publish_frame()` counter aggregation and `flush_counters()` on lean REFERENCE frames
- `timestamp_event` / forensic guards on REFERENCE
- `eu4_scope_snapshot()` on publish
- REFERENCE render/present timing and scope wrappers
- GL shadow / preparation on REFERENCE paths (unchanged WP5 rules)

### Retained on published frames

Minimum for Tier-1 and attribution:

- `update_id`, `phase`, `measurement_epoch`, `generation`, `thread_id`
- `wall_ns`, `cpu_ns`, `start_ns`, `end_ns`
- `update_wall_ns` / `update_cpu_ns` (same envelope as wall/cpu for lean path)
- Validity flags when publication gates fail

Additional fields require explicit cost justification before reintroduction.

## Validity contract — `wp7_lean_reference_validity_v1`

Offline `reference` stages **no longer require** scope-tree reconciliation.

Required:

- Exactly **N** eligible `F` rows (Tier-1: 4)
- Strictly increasing `update_id`
- `measurement_epoch` and `generation` present on each row
- Non-zero `wall_ns` and `cpu_ns`
- Non-zero `thread_id`; `end_ns > start_ns`
- No boundary/writer failure flags (`1|512|1024|2048|4096`)
- Terminal `Z`: `hook_failures == 0`, `dropped_records == 0`

This is an **implementation-aware** structural contract, not a relaxation of Tier-1 ±3% / ±50 µs acceptance.

## Test-only stages (unchanged)

| Contract | Role |
|----------|------|
| `wp6_loaded_disabled_v2` | Passive hooks, `MEASURE_ENABLED` off |
| `wp6_minimal_reference_v2` | Passive hooks, `MEASURE_ENABLED` on, no publish |

Production lean REFERENCE is **neither** of the above; it publishes minimal frames.

## PR B — training requalification

After PR A merges, on a **clean tree**:

```bash
# Existing Tier-1 offline training qualification (seven-pair bare → reference → counters).
python3 benchmark/eu4_frame_model.py <tier1-training-command>  # see wp7-training-requalification.md
```

Question: did lean production REFERENCE become cheap enough for the existing policy?

- **Pass** → held-out qualification → live EU IV capture program
- **Fail with small `reference − loaded-disabled` but material `loaded − bare`** → interposer guard optimization or revisit relative ±3% on tiny CPU denominators (policy decision after data)

## Non-goals

- Marginal REFERENCE ablations (scopes-only, events-only, …)
- Another `reference-cpu-decomposition` or `minimal-reference-reconciliation` capture
- Changing PROFILE / counters / forensic semantics
- Changing Tier-1 acceptance thresholds in PR A

## Related

- [WP6 ladder memo](wp6-reference-cpu-ladder-memo-20261003.md) — authoritative ladder `4dc19f16`, reconciliation `a1dfde61`
- [WP6 plan](wp6-reference-cpu-decomposition.md)
