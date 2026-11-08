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

## Scope and hygiene

Editable paths are in `contract.json`. The candidate version must not modify
`automation/gfx_draw_accept.py`, `automation/gfx_draw_accept_v2.py`,
`automation/gfx_contracts/api.py`, `automation/gfx_contracts/v0.py`, the
checkpoint, or any trusted manifest. Keep `progress.md` current.
