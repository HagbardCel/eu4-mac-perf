# L3.2b-II — Complete the authentic Simple draw packet

You are reconstructing the remaining source inputs consumed by the original
EU4 loading-screen draw selected by Decision 0003 (`_GfxDraw` at caller RVA
`0xedfcd1`, mode 5, effective count 4, non-null shader).

Work only in this workspace. The authoritative L3.2a probe and the L3.2b-I
effective-state evidence are frozen. Do not modify them.

## Objective

Produce one self-contained, qualified original-EU4 draw packet that establishes
every required input under the declared L3.2b replay contract. The agent — not
the operator — chooses which functions to research and instrument.

Start with an inventory, not a fixed hook list:

- Which inputs can be established from the existing draw master and object
  graph at draw time?
- Which creation-time bytes are not retained and need an entry observer
  (shader source strings, texture upload pixels, texture binding, scissor)?
- Does shader/effect creation happen before or after the `_GfxInit` admission
  barrier?
- What is the measured texture size and mutation history?
- What is the minimum simultaneous target set for the final capture?

Prefer draw-time object reads over new gateways. Expand beyond eight installed
targets only if the measured final target set requires it, with ABI-safe
gateway tests and independent review. Expand payload or record bounds only
after measurement. Never instrument the unsafe interior `_GfxUpdateTexture`
site without a separate call-through qualification.

## Provisional checkpoints

When every observable input you currently claim is structurally valid but some
have no trusted validator, the checker writes a durable provisional checkpoint.
A partial checkpoint is expected: continue investigating, and the coordinator
will qualify the provisional fields before the next continuation campaign.
A checkpoint is not acceptance of L3.2b.

## Final capture

The final packet must come from one fresh coherent original-game session and
must contain every required source byte and sidecar. Exploratory captures may
inform implementation but must not be combined into the final packet. Do not
claim `packet_ready=true`; the checker computes readiness and writes the trusted
qualification receipt.

## Scope and hygiene

Editable paths are in `l32b.json`. Do not edit the trusted checker, contracts,
campaign files, manifest, L3.2a/L3.2b-I evidence, `target.json`, or Metal/worker
files. Use campaign captures only (`--campaign-run-root`), never
`--authorize-live`. The global ledger owns the budget; do not edit it.

Keep `progress.md` current with the inventory, capture questions, established
findings, blockers and the next action.
