# L3.2b contract qualification

You are qualifying one or more field-specific evidence validators for a
provisional checkpoint. The checkpoint contains original-execution evidence
that the trusted checker could structurally validate but could not yet accept
because no validator exists for those fields.

Work only in this workspace. Do not launch EU4. Do not modify the observer,
the trusted checker, the contract API, existing contract versions, campaigns,
or the oracle.

## Required outcome

For the checkpoint's `contract_gaps` fields:

1. Write `automation/gfx_contracts/<version>.py` with `VALIDATORS` mapping each
   field to a callable `(value, ctx) -> str | None`.
2. Write `automation/gfx_contracts/<version>.spec.json` with, per field:
   - the value schema;
   - the original-code evidence references (path + SHA-256);
   - the authoritative representation and assumptions;
   - at least one meaningful adversarial mutation class and expected error.
3. Write `tests/test_gfx_contract_<version>.py` as regression coverage.
4. The validator must accept the checkpoint's original evidence and reject
   incorrect-but-structurally-valid evidence.

The independent reviewer will author additional field-specific adversarial
cases outside this worktree. A validator that only rejects its own synthetic
fixtures is not qualified.

## Reviewer adversarial artifact

The reviewer must write `review-adversarial.json` next to `review.json`, with:

- `schema: "eu4-gfx-adversarial-cases/v1"`;
- the exact reviewed contract commit, contract version and checkpoint SHA-256;
- at least one case per newly qualified field.

Each case must have a meaningful `mutation_class` (for example
`wrong_selected_object`, `cross_resource`, `wrong_pixels`, `stale_call`,
`cross_session`, `malformed_association`, `wrong_state_decode`), a
`structural_validity` of `valid`, forged value and evidence that reference
genuine original records, an explanation of why the case is semantically wrong,
and the expected error prefix.

The trusted runner resolves every forged evidence item against genuine original
records. A case with a nonexistent call id or mismatched payload is an invalid
test, not a semantic rejection. The coordinator requires every case to be
rejected for the stated reason, and it reruns the cases immediately before
integration; a changed contract, checkpoint, report or case artifact blocks
promotion.

Validators must inspect `ctx.field_evidence` / `ctx.field_evidence_paths` for
association checks. A `draw_record` alone is not shader-source evidence.

## Scope and hygiene

Editable paths are in `contract.json`. The candidate version must not modify
`automation/gfx_draw_accept.py`, `automation/gfx_draw_accept_v2.py`,
`automation/gfx_contracts/api.py`, `automation/gfx_contracts/v0.py`, the
checkpoint, or any trusted manifest. Keep `progress.md` current.
