# Agent defaults

Current capabilities, evidence status and open limits are recorded in
[docs/status.md](docs/status.md). Consult it before planning graphics work or
claiming that a capability exists.

## Verification workflow

Use [automation/test_runner.py](automation/test_runner.py) instead of starting
long `unittest` commands repeatedly. It preserves the child exit code, writes
the complete log under `.local/test-runs/`, returns a compact summary, and
returns an already-running identical suite instead of launching a duplicate.

1. **After a code change**, run the focused modules that exercise it:
   ```sh
   python3.14 -m automation.test_runner run --profile offline \
     --module tests.test_agent_tools --module tests.test_gfx_case
   ```
2. **Once focused tests pass**, run the dependent modules they touch (the
   neighbouring suites named in the change or its plan).
3. **Run the full offline gate once at the end**:
   `python3.14 -m automation.test_runner run --profile offline --label full`.
   The offline profile is the default and cannot launch EU4. Live tests are
   explicit: `--profile live --module <module>`, with a real case and bounded
   timeout, and are never part of the default gate.
4. **Compare failures against base** before claiming a regression:
   run the same focused modules in a base worktree (`--cwd`) and use
   `python3 -m automation.test_runner compare --base <id|record> --candidate <id|record>`.
   A pre-existing failure is reported as pre-existing, not fixed or introduced.
5. Inspect a running or finished invocation with
   `python3 -m automation.test_runner status --id <id>`; do not poll a log file.

The full offline gate is the final verification step for retained implementation
changes or PR review, not for temporary diagnostic modifications, exactly
reverted experiments, individual `/goal` rounds, or campaigns whose acceptance
is determined by the existing independent checker. During autonomous
reconstruction, run the focused tests relevant to retained changes and the
existing trusted checker instead; do not modify `automation/test_runner.py` or
add test caching.

When reverting an experiment, compare the renderer source or diff against the
saved pre-experiment state. If it is identical, reuse the earlier verification
evidence. If it is not identical, investigate the difference before claiming
restoration.

`python3.14 -m automation.python_runtime resolve` is the checked interpreter
for the diagnostic core. The profile split is enforced by
`automation.test_profiles` and by the capture/launch entry points: an explicit
`EU4_TEST_PROFILE=offline` (or `EU4_OFFLINE_ONLY=1`) refuses a real EU4 launch. If a native tool reports an interpreter or NumPy mismatch,
fix `EU4_DSH_PYTHON` rather than running the tool again with another Python.

## Evidence discovery

Historical campaigns are indexed, read-only, through
`python3 -m automation.agent_tools eu4_session discover`. Set
`EU4_EVIDENCE_ROOT` (OS-path-separated) to the worktrees whose `.local` evidence
should be visible. Discovery never searches the filesystem, copies artifacts or
mutates historical evidence; select a session with `eu4_diff_inspect --run`.

## DSH permission discipline

- Use normal tool execution without sandbox escalation by default.
- Do not request `sandbox_permissions` proactively for
  file inspection, process monitoring, tests, or builds.
- Retry with elevated permissions only after a real
  sandbox denial, and only when necessary.
- Keep compiler caches and temporary build outputs
  inside the workspace where practical.
- Do not repeatedly request elevated permissions
  for equivalent diagnostic commands.

## EU4 reconstruction and migration

For cohesive reconstruction or migration of EU4 functionality—especially `_Gfx*` OpenGL→Metal work (shaders, render state, draws, lifecycle remediations)—**do not** implement the migration body by hand in an interactive session.

**Default path:** run an OpenCode campaign through `python3 -m automation.cli` with the appropriate skill (load `opengl-metal-reconstruction` when replacing OpenGL graphics calls) and an independent campaign checker under `campaigns/`.

**Native DSH exception:** when the operator starts a `/goal` reconstruction experiment through DSH Web, DSH owns orchestration. Work directly in the session's authorized worktree; do not launch the OpenCode runner and do not build a replacement agent loop. OpenCode remains the supported default for campaign-run work.

**Interactive / human focus:** semantic constraints, acceptance criteria, evidence review, and deciding genuine ambiguities or `BLOCKER:` lines.

See [automation/README.md](automation/README.md) for the runner, worktree pin, and session contract. Campaign layout lives under `campaigns/`. A checker `pass` is offline qualification only; independent PR review remains the merge gate.

### Reusable reconstruction capabilities

All new graphics reconstruction and migration work should strengthen the autonomous investigation, implementation and validation workflow.

Prefer functionality that is independently usable by DSH and OpenCode through existing command-line tools.

When implementing a new rendering capability:

- Reuse existing graphics-state reconstruction, backend lifecycle, evidence capture, replay and validation.
- Separate game-independent mechanisms from function-specific semantic knowledge.
- Make new observation or validation functionality reusable across graphics operations.
- Demonstrate reuse with an existing second case when practical.
- Avoid one-off launchers, capture scripts, agent orchestration and duplicate rendering implementations.
- Do not generalize speculative future requirements. Extend shared abstractions only where a real use case requires them.
- Preserve explicit evidence provenance, unsupported-state diagnostics and independently reviewable outcomes.

The objective is not to maximize tooling. It is to minimize the engineering required for each subsequent authentic graphics reconstruction.

## L3.2b completion

For the L3.2b-II draw-packet completion campaign, do not pre-assign `_Gfx*`
functions or predefine shader → texture → state contract versions. Use the
dynamic coordinator ([automation/gfx_l32b_complete.py](automation/gfx_l32b_complete.py))
and let the agent inventory the unresolved fields, choose draw-time reads or
minimal entry observers, and produce provisional checkpoints. Contract
qualification is a separate campaign with an independent reviewer; a
`qualified` review outcome is required. The v1 checker
`automation/gfx_draw_accept.py` is frozen by the completed L3.2b-I manifest; new
campaigns use `automation/gfx_draw_accept_v2.py`. Original shader and texture
bytes stay under `.local`; only hashes, lengths and provenance are tracked.

## Differential debugging defaults

Before starting another live EU4 run:

1. query existing Ghidra/engine-contract knowledge (`eu4_re_query`);
2. run the focused native/static tests;
3. replay known local cases (`eu4_replay`);
4. inspect the recorded differential observations (`eu4_diff_inspect`,
   `eu4_capture health/frames`);
5. use one targeted live diagnostic if the evidence is insufficient;
6. run a dual-process campaign only when no adequate offline case exists;
7. run the full qualification after corrections.

Use `python3 -m automation.agent_tools <tool> ...` (or the native DSH tools) for
these steps; do not compose new launch/capture scripts. A new EU4 launch needs a
specific diagnostic objective and an operator-issued one-use launch
authorization for a `controlled_capture` campaign; the offline autonomous
campaign cannot launch EU4. If two
launches for the same defect are inconclusive, redesign the instrumentation or
experiment before a third. Use normal permissions first; request sandbox
escalation only after a genuine denial.

`eu4_capture health` is the stop signal: a rejected draw, pipeline/validation
failure, failed resolve/readback or failed command buffer ends the current
diagnostic result. Command-buffer completion is not pixel correctness.
