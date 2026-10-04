# Border multidraw mutation topology

## Architecture verdict (PR B)

| Option | Mutation verdict |
|--------|------------------|
| **Loop-head / run-head** @ `0x1010cbe55` | **Candidate** — inspect full record run and barriers **before** per-record `GfxSetVertexBuffers` / constant-buffer / draw setup |
| Run-head gather + skip loop | Requires Gate **3b** synthetic post-loop state |
| Leaf `GfxDrawIndexed` patch (3 sites) | Fragile; still downstream of per-record setup |
| **`glDrawElementsBaseVertex` interpose (deferred flush)** | **NO-GO for mutation** — see below |

### Why GL-tail interpose fails Gates 2 and 3a

By the time draw \(N+1\) reaches `glDrawElementsBaseVertex`, the engine has already executed record \(N+1\) setup (VBO table index, IBO bind, color/constant-buffer path, etc.). A deferred flush of draw \(N\) at that point executes under **draw \(N+1\)'s GL/engine state**.

Additional discontinuity: `GfxDrawIndexed` tail-jumps to `glDrawElementsBaseVertex` only when base vertex ≠ 0; **base vertex 0** may use `glDrawElements`, bypassing the interpose while a deferred queue exists.

The interpose may still serve as a **pass-through observer** (immediate `real_draw`, TLS aggregation, phase-boundary counter publish). It is **not** an authorized mutation topology.

## Gate 3a — temporal equivalence

**FAIL** for deferred interpose mutation.

**PASS (conditional)** only for loop-head batches that exclude:

- slow color / `GfxUpdateConstantBuffer` per-record paths,
- VBO table index transitions,
- mode ≠ 0 branches,
- site 2 `+0x10` helper semantics.

## Gate 3b — post-loop state equivalence

**Required** for loop-head/run-head skip. Documented synthetic exit at `0x1010cc3bd` (see prior revision). **N/A** for observer interpose.

## Argument marshalling (loop-head target)

| Question | Answer |
|----------|--------|
| Pre-built arrays? | **No** — linear gather from 28-byte stride before first mutating setup |
| Scratch | TLS/stack; **no heap** in render loop |
| `indices[i]` | **0** (border path) |

## Hard realizability verdict

**NO-GO** for Venice mutation via GL interpose.

**Next implementation:** loop-head interception proof + patch (preferred address `0x1010cbe55`); PR C harness remains **observer-only** until that lands.
