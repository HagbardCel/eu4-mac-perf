# Architecture decisions

Accepted architectural decisions will be recorded here as short ADRs (immutable once accepted; supersede with a new record rather than editing history).

| Record | Decision |
| --- | --- |
| [0001](0001-semantic-gfx-then-metal.md) | Semantic reading of the frozen `_Gfx*` surface, then Metal in execution order |
| [0002](0002-first-live-metal-draw.md) | Next runtime target is the first eligible game-owned Metal draw, not a full frame |

Older rendering gates remain in [eu4_recommended_strategy.md](../eu4_recommended_strategy.md) until a later record extracts them.
