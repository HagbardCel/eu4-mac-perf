# Optimized Metal backend (planned)

This backend is **planned**, not implemented. It must not be compared to the
faithful backend as if it were a correctness reference. The faithful backend
and the original sealed OpenGL evidence remain the authority.

## Preconditions

Do not start optimization until all of the following are true:

1. the faithful backend reaches exact equality on a named supported workload;
2. that workload has full state coverage and a protected negative control;
3. profiling identifies one measured bottleneck with a credible mechanism;
4. the optimization has a separate acceptance criterion (for example, GPU time
   or allocation), not a relaxed pixel tolerance.

Until then, no performance number from this backend is a qualification claim.

## Constraints

- Same sealed case and same reference as the faithful backend.
- No shader arithmetic shortcuts that are not independently proven equivalent.
- No reduced state coverage, smaller viewport, skipped draws or masked
  channels.
- Each optimization is independently reversible and compared A/B/A on the same
  workload.
- Report host, driver, power/display state, cache state and exact workload
  identity with every timing.

## Measurement plan

- Primary metric: GPU service time where actually sampled, plus end-to-end
  client wall clock separately.
- Report unknown GPU time as unknown; do not infer it from wall clock.
- Use paired cold/warm trials with identical starting knowledge and at least
  three repetitions per comparable task.
- Preserve failed and blocked trials.

## Relationship to the faithful backend

The optimized backend may reuse resource containers, the graph, the comparator
and the receipt schema. It may not reuse a faithful result as proof that an
optimized variant is correct; the optimized variant needs its own exact
comparison and its own protected control.

See [Metal backend](metal.md), [status](status.md) and the
[rendering strategy](eu4_recommended_strategy.md).
