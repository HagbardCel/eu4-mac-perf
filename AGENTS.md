# Agent defaults

Read [current status](docs/status.md) before planning renderer work or claiming
a capability. Use the [renderer index](docs/renderer/README.md) for contracts and
evidence. The [permanent strategy](docs/eu4_recommended_strategy.md) defines
ownership and the order of reconstruction, qualification and migration.

## Architecture and evidence

- Original EU4 is the ultimate scientific authority. Reconstruct the relevant
  OpenGL behavior faithfully before migrating it to Metal.
- Use reconstructed GL as a reference only for independently qualified scopes.
  Distinguish native original observations, emulated-original results and
  synthetic fixtures. Preserve their executable, environment and seal identity.
- Metal is the production target. Measure optimizations after correctness is
  established. Production rendering does not depend on GL/Metal interoperation.
- Name reusable modules, tests and CLI operations by function. Milestone IDs
  belong in evidence, protected cases, historical reports and reproduction
  commands. Maintained execution must not depend on planning documents or
  historical campaign wrappers.
- Identify the existing capability each nontrivial infrastructure change extends
  or replaces. Retire superseded paths after verified consumer migration.
  Reuse capture, replay, comparison, receipt and candidate-build owners. Consolidate before
  renaming. Count pure moves as zero and measure enabling changes separately
  from scientific functionality. Aim for nonpositive net enabling LOC and
  obtain explicit review for exceptions.

- Maintain canonical function and component knowledge linked to the implementation,
  evidence, qualified scope, uncertainties and negative outcomes.

## Qualification

- Use protected automatic reference selection and independent qualification.
  Candidate inputs cannot narrow mandatory coverage.
- Verify the immutable original seal, candidate revision, effective command,
  execution environment, observations and output bytes independently.
- Keep original evidence, derived portable evidence and restricted candidate
  inputs distinct. Keep protected expectations and signing keys on the
  verifier side. Document shared-address-space limits and unsupported scopes.
- Require exact observations for exact claims. Keep tolerated policy outcomes,
  execution success and evidence verification distinct from qualification.
- Verify historical receipt authenticity and artifacts without rebuilding.
  Current-candidate applicability requires a trusted check of the relevant
  source closure, recipe, environment and reference. Aggregate verification
  checks protected policy and signed case decisions.
- Discover retained evidence with `python3 -m automation.agent_tools
  eu4_session discover`. Set `EU4_EVIDENCE_ROOT` to the intended worktrees.
  Discovery is read-only. Select a session with `eu4_diff_inspect --run`.
  Keep original shader and texture payloads under `.local`. Track hashes,
  lengths, and provenance.

## Supported runtime and permanent direction

For cohesive reconstruction or migration of EU4 renderer functionality, use
the supported OpenCode campaign runtime through `python3 -m automation.cli`
and the relevant skill and independent checker. Load
`opengl-metal-reconstruction` when replacing OpenGL calls. Interactive work
covers contracts, review, evidence and tooling.

When an operator starts a native DSH `/goal` reconstruction experiment, work
in its authorized worktree through DSH. DSH owns its orchestration. The permanent
target is native DSH continuation with repository-owned scientific methodology
and minimal safety enforcement. Its default autonomous session lasts two hours.
The target requires recoverable continuation and Git checkpoints after changed
rounds and before continuation. R4 owns these lifecycle guarantees. See [runtime contracts](automation/README.md) and
[the approved refactoring plan](plans/repository-refactor/approved-plan.md).

## Verification workflow

Use [test_runner](automation/test_runner.py). It records complete logs under
`.local/test-runs/` and avoids starting duplicate running suites.

1. Run the focused modules after a change with `python3.14 -m
   automation.test_runner run --profile offline --module <module>`.
2. Run the dependent modules after the focused checks pass.
3. For a retained implementation PR, run one full offline gate with
   `python3.14 -m automation.test_runner run --profile offline --label full`.
   Temporary probes, exact rollbacks and individual autonomous rounds use
   focused checks instead. Use the independent checker for retained
   renderer corrections.
4. Compare unexpected failures against the same focused selection in a base
   worktree using `--cwd` and `test_runner compare --base <id|record>
   --candidate <id|record>`.
5. Use `test_runner status --id <id>` for progress and results.

Keep `test_runner.py` unchanged and avoid test caching. A valid build may be
reused after a byte-identical rollback. Resolve the diagnostic interpreter
with `python3.14 -m automation.python_runtime resolve`. Fix
`EU4_DSH_PYTHON` if a native tool reports an interpreter or NumPy mismatch.

## Capture and debugging

Query existing engine knowledge and run focused checks first. Replay retained
cases, inspect capture health and the first divergent event, then narrow the
question before any live diagnostic. Use the maintained
`automation.agent_tools` commands or native DSH tools.

Offline qualification cannot launch EU4. For live development captures, use a
specific diagnostic objective, a fresh output path and a bounded timeout.
Stop on capture-health failure or incomplete evidence. Active campaigns also
apply their mode, deadline and stop state. Use normal execution permissions and keep temporary outputs within
the authorized worktree where practical. For the historical draw-packet
completion campaign, follow its [coordinator contract](automation/gfx_l32b_complete.py);
the frozen v1 checker remains historical and v2 owns new campaign checks.
