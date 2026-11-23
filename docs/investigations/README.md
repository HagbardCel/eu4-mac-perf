# Investigations index

Detailed technical analyses remain in their original paths during migration. Prefer linking from findings rather than duplicating conclusions.

| Topic | Starting points |
| ----- | ---------------- |
| Draw path screening | [../../analysis/draw-path-decision.md](../../analysis/draw-path-decision.md) |
| Border multidraw / loop head | [../../analysis/border-multidraw-re.md](../../analysis/border-multidraw-re.md), [gfx-border-multidraw-ababa.md](../gfx-border-multidraw-ababa.md) |
| Frame model / profiler | [../../analysis/frame-model-verification.md](../../analysis/frame-model-verification.md), [../../analysis/frame-model-protocol.md](../../analysis/frame-model-protocol.md) |
| Gfx subrecord observers | [gfx-subrecord-ababa.md](../gfx-subrecord-ababa.md), [../../analysis/mesh-observer-chain-semantics-20261004.md](../../analysis/mesh-observer-chain-semantics-20261004.md) |
| Metal sessions: graphics stack | [eu4-graphics-stack-metal-experiments.md](eu4-graphics-stack-metal-experiments.md) |
| Metal sessions: what the probes tested | [metal-probe-approaches.md](metal-probe-approaches.md) |

Generated JSON under `analysis/evidence/` supports measurements; interpretation belongs in findings or status.

## Retired narratives

The October–November 2026 profiler/roadmap notes, WP-series memos, Stage-2 and
Tier-1 planning narratives, and the historical roadmap were retired because
their canonical results and current limits live in [../status.md](../status.md)
and the active documents above. The removed text remains in git history; no
measured evidence was deleted.

`docs/repo-lifecycle.csv`, `docs/repo-inventory.csv`, `docs/repo-edges.csv` and
`docs/repo-map.md` are generated snapshots pinned by `docs/repo-audit-context.json`
(`source.ref`), not assertions about the current tree. Regenerate with
`python3 -m tooling.common.repo_audit --write`; do not edit them by hand.
