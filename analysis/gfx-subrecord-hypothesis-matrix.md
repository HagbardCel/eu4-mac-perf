# Gfx subrecord multi-hypothesis matrix (PR A exit)

Protocol v3 uses **one bit index per hypothesis** in both `supported_hypothesis_mask` and `safety_qualified_hypothesis_mask` ([`submission_counter_schema.json`](../benchmark/submission_counter_schema.json)).

## Hypothesis table

| Bit | `hypothesis_id` | Structural counter(s) | Safety-qualified counter | Supported (PR B) | Safety-qualified (PR B) | Venice outcome → next action |
|-----|-----------------|----------------------|---------------------------|------------------|---------------------------|------------------------------|
| 0 | `same_parent_buffer_signature` | `same_parent_buffer_signature` | (v1 `eligible_pair_hits` alias) | yes | yes (v1 predicate) | **Closed** — Venice v1 showed ~0% on paused fixture |
| 1 | `cross_parent_buffer_signature` | `cross_parent_buffer_signature` | — | yes | no | High rate → deepen GfxDrawIndexed continuity RE; low → deprioritize cross-parent buffers |
| 2 | `cross_parent_buffer_elision` | — | `cross_parent_buffer_elision_eligible` | yes | **no** (PR A NO-GO) | If later GO + material → cap 2 design; else stay observer-only |
| 3 | `same_subrecord_pointer_cross_parent` | `same_subrecord_pointer_cross_parent` | — | yes | no | High → static RE on shared mesh assets; never Stage-2 alone |
| 4 | `same_texture_input_signature` | `same_texture_input_signature` | — | yes | no | High → texture barrier RE; low → texture path cold |
| 5 | `texture_setup_elision` | — | `texture_setup_elision_eligible` | no | no | Unsupported until layout barriers resolved |
| 6 | `object_constants_elision` | — | `object_constants_elision_eligible` | no | no | Unsupported; cross-parent unlikely |

## PR B decision-value gate: **GO**

Proceed to PR B because:

1. **Cross-parent buffer signature** — structural counter can discriminate Case A vs B from v1 smoke (denominator algebra).
2. **Same subrecord pointer** — cheap structural signal for instanced mesh reuse.
3. **Texture input signature** — second actionable structural branch if rates > 0.
4. Safety-qualified cross-parent buffer elision remains **off** until continuity proof completes — PR B still valuable without mutation path.

**NO-GO example (not current):** only bit 3 supported and all follow-ups are “more RE” with no threshold decisions.

## Materiality (pre-registered)

Measure on B1 **ARM→FREEZE** window only:

```text
hits_per_swap = hits / measurement_paused_swaps
estimated_removable_helper_calls_per_swap = k × hits_per_swap
```

| Hypothesis | `k` (helpers removable per hit) | Stage-2 floor (illustrative) |
|------------|----------------------------------|------------------------------|
| `cross_parent_buffer_elision` | 2 (VBO + IBO helper) | ≥ 0.02 removable calls/swap **and** safety-qualified YES |
| `texture_setup_elision` | TBD from removed-work table | unsupported until safety GO |

Do **not** recommend Stage 2 on hit rate alone; require safety-qualified mask **and** removable-work threshold.

## Per-hypothesis status taxonomy (smoke output)

`unsupported` | `not_exercised` | `evaluated_no_recurrence` | `recurrence_observed` | `material`

Harness fails only on protocol/hook/scene/invariant/`effective_actions`; **not** on `not_exercised`.
