# L3.2b infrastructure bootstrap

You are building the thin trusted infrastructure that lets the completion loop
advance from a provisional evidence checkpoint without treating it as a
successful reconstruction.

Work only in this workspace. Do not launch EU4. Do not implement Metal, shader
interception, a gateway rewrite, a generic mutation engine, or predefined
shader/texture/state contract versions.

## Required outcome

Implement exactly this scope:

1. `automation/gfx_draw_accept_v2.py` — a new immutable checker revision. Do not
   modify `automation/gfx_draw_accept.py`; the completed L3.2b-I campaign's
   manifest pins it.
   - Fail-closed dispatch: a required field with no validator is `provisional`,
     never accepted.
   - Base validators for `draw_invocation`, `primitive_semantics`, `viewport`,
     `render_target`; retain the existing three validators.
   - Evidence support for `draw_record`, `primitive_set`, `static_table`;
     executable-verified `static_reconstruction`.
   - Provisional checkpoints, partial or complete, written as durable bundles
     outside the candidate worktree and the temporary packet directory.
   - Trusted qualification receipt; `packet_ready` computed by the checker.
   - Two-identity campaign loading (`candidate_base_commit` and
     `trusted_contract_commit`).
   - Global-ledger-authoritative capture selection.
2. `automation/gfx_contracts/` — minimal versioned contract API and `v0`.
3. `automation/gfx_checkpoint.py` — durable bundle writer/validator.
4. `automation/launch_ledger.py`, `automation/live_gfx.py`,
   `automation/state.py` — one crash-safe global ledger with `reserved`,
   `started`, `completed`, `failed`, `abandoned`; no automatic refund.
5. `automation/gfx_contract_runner.py` and `automation/gfx_contract_accept.py` —
   separate-process candidate validator execution, checkpoint positive evidence,
   reviewer adversarial cases.
6. `automation/gfx_l32b_complete.py` — dynamic queue coordinator with injected
   hooks; checkpoint validation, snapshot, contract qualification, reviewer
   outcome enforcement, integration, continuation, bounded research, stop
   conditions.
7. `benchmark/eu4_gfx_draw_packet.py` — `value` for the four base fields;
   `render_target` observed/assumed split; `packet_ready` only from all 17
   statuses.
8. `campaigns/eu4-l32b-complete/` and `campaigns/eu4-l32b-contract/` templates,
   tests, docs and the decision record.

The frozen acceptance suite is `tests/test_gfx_infra_frozen.py`, run from the
trusted checkout. It defines the required behavior, including the synthetic
partial-checkpoint → qualification → reviewer → integration → continuation path
and the crash/restart ledger behavior. Candidate tests are regression coverage
only.

## Scope and hygiene

Editable: the paths in `infra.json`. Do not edit the frozen checker
`automation/gfx_infra_accept.py`, the L3.2b-I checker, the L3.2a oracle,
`analysis/qualification/v0/`, `reverse_engineering/target.json`, the observer
gateway, Metal/worker files, or any path outside the allowed surface.

Keep `progress.md` current. A checker `pass` is offline qualification for
independent review, not merge approval.
