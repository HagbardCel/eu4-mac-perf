# EU IV macOS rendering strategy

The project aims to improve the performance of native EU IV on macOS while
preserving rendering behavior. [Current status](status.md) records what has
actually been demonstrated, with links to the underlying runs and findings.

## Scientific authority

Original EU IV is the ultimate authority. Pin the executable and environment,
retain the original observations and record how they were captured. Treat the
same executable observed on a different architecture, GL implementation,
context or macOS build as a distinct applicability question. Reuse sealed
evidence for offline investigations and retain negative outcomes alongside
successful comparisons. [Evidence retention](evidence-retention.md)

Reconstruct the applicable OpenGL semantics faithfully before migrating that
behavior to Metal. Independent qualification must establish the state,
resource, command, output and environment scope. Reconstructed GL becomes a
reference only inside that qualified scope. Preserve the distinction between
the original native program, an emulated original function, reconstructed
behavior and synthetic controls. [Scientific charter](renderer-reconstruction-and-differential-validation.md)

## Production architecture

Use the engine's Gfx abstraction as the migration seam. Keep ABI integration,
engine semantics and backend execution under separate functional ownership.
Metal is the native production target. Production rendering has no dependency
on GL/Metal interoperability. OpenGL remains available for faithful
reconstruction and independent comparison. [Renderer knowledge](renderer/README.md)

Start with a correct measurable implementation. Evaluate changes against the
same workload and relevant baseline, recording the hardware, settings, phase
of execution and measured quantities. Report CPU and GPU work separately where
the instrumentation supports it. Maintain unique temporal, structural and
regional diagnostics alongside exact attachment comparisons.

## Independent execution and review

Use protected policy to select evidence, define required observations and
judge exactness. Stage restricted candidate inputs apart from the original
reference and independently measure observed output and negative controls.
Authenticate receipts and verify their artifacts. For receipt reuse, determine
whether the current candidate shares the relevant source closure, recipe,
environment and reference. Verification of an already-issued historical
execution receipt does not rebuild a candidate. Verify the aggregate against
the signed case judgments and protected policy. A verified incomplete decision
remains incomplete. [Qualification operations](agent-tools.md)

Maintain canonical knowledge entries for functions and components. Link each
entry to implementation, evidence and the independently qualified scope. Record
uncertainties, negative outcomes and the conditions needed to resolve them.

Keep synthetic passes, execution health, tolerated comparisons and exact
qualification distinct. Independent review of the retained revision and its
evidence is required before promotion.

## Repository ownership and runtime

Reusable implementation is milestone-independent. Maintain functional owners
for capture, input staging, qualification, comparison and reporting. Keep
historical IDs in evidence, protected cases, reports and justified reproduction
entry points. Use the shared mechanisms instead of building per-experiment
orchestration. Each nontrivial infrastructure addition names the capability it
extends or replaces. Retire superseded implementations after verified consumer
migration. Measure enabling refactoring separately from scientific
functionality. Aim for a nonpositive enabling change and review any exception
explicitly. Pure moves carry no reduction credit. [Refactoring plan](../plans/repository-refactor/approved-plan.md)

The repository owns scientific methodology, protected qualification and
minimal safety enforcement. OpenCode remains the supported transitional
campaign runtime. Native DSH owns autonomous continuation in the permanent
target and already operates explicitly authorized DSH experiments. The
permanent default is a two-hour autonomous session with recoverable native
continuation. Checkpoint the authorized Git worktree after each changed round
and before continuation. R4 owns those lifecycle guarantees. [Runtime contracts](../automation/README.md)

The earlier domain-by-domain roadmap, its performance hypotheses and the
exploratory GL/Metal interoperation rationale are preserved in the
[previous strategy revision](https://github.com/HagbardCel/eu4-mac-perf/blob/5ecde67cefb36aeb517bc7ddba92987ee95b9cf0/docs/eu4_recommended_strategy.md).
