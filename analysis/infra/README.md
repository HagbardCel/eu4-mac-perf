# Differential debugging infrastructure — index

Stacked implementation on top of PR #189 (`f2cd8f9`), in separate branches in
the `wt_infra` worktree. The M6 branch/worktree was not modified.

| PR | Branch | Focus |
| --- | --- | --- |
| 1 | `infra/pr1-diagnostics` | [diagnostic integrity](diagnostic-integrity-audit.md), capture validity, GPU health, owned sessions, bounded artifacts |
| 2 | `infra/pr2-dual-runner` | [dual-process campaigns](dual-process-campaigns.md), [feasibility spike](dual-process-feasibility.md) |
| 3 | `infra/pr3-differential-compare` | [alignment and comparison](frame-alignment-and-comparison.md) |
| 4 | `infra/pr4-first-divergence` | [first divergence and offline cases](first-divergence-and-offline-cases.md) |
| 5 | `infra/pr5-agent-tools` | [agent integration and efficiency](agent-integration-and-efficiency.md) |

Machine-readable evidence:
[feasibility](dual-process-feasibility.json),
[campaign runs](dual-process-campaigns.json).

Operational entry points:

```sh
python3 -m automation.agent_tools eu4_re_query resolve SShaderOpenGL
python3 -m automation.agent_tools eu4_diff_inspect --run <session-dir> --build-case
python3 -m automation.agent_tools eu4_case_inspect --case <case-dir>
python3 -m automation.gfx_metrics report --root <campaign-root> --ledger <ledger>
```

Status and limits are authoritative in [docs/status.md](../../docs/status.md).
