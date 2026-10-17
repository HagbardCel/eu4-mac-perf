# OpenCode integration feasibility — verified capability matrix

**Scope:** Public OpenCode documentation read 2026-10-16. Not tested in a running OpenCode installation in this environment. The Python oracle prototype in this bundle is tested offline, but there is no original EU IV runtime trace.

## Supported by public OpenCode documentation

| Need | Documented API | Implementation implication |
|---|---|---|
| Single specialist | project `.opencode/agents/*.md` or `agent` config, `mode: primary` | One persistent reasoning actor *per function* |
| No delegation | `permission.task: deny` | Explicitly disable subagent calls |
| Open local evidence | Custom project tools `.opencode/tools/*.ts` with `tool` from `@opencode-ai/plugin` | Use typed oracle wrappers; avoid large context dumps |
| Python/Rust oracle | TS custom tool `execute` may invoke external scripts/programs | Wrapper delegates to subprocess/service |
| Controlled candidate edits | Built-in edit/write + permission policies | Expose only worktree; enforce with OS sandbox |
| Machine control | `@opencode-ai/sdk`, `createOpencode`, `client.session.create`, `prompt`, `messages`, `abort` | Deterministic outer work queue and caps |
| Non-interactive pilot | `opencode run --agent <agent> ... --format json` | Quick tool-resolution smoke |
| Per-prompt agent step limit | `steps` in agent configuration | Secondary budget; controller remains authoritative |
| Typed result reporting | SDK structured output JSON schema | Optional agent status; runner's certification is final |

## Not provided by OpenCode

- Exact EU IV function ABI resolution, hooks, inputs/outputs, game resource/state checkpoints.
- Guaranteed function equivalence, hidden-trace isolation, trace comparators, test coverage inference.
- Complete security sandbox for native compiled code or hardened protection of original blobs.
- Cross-session monetary billing ceilings; impose conservative ledgers and per-call caps independently.
- Verified interoperability with an **uninstalled** local OpenCode release. Install/pin version and execute a 3-tool synthetic smoke before collector work proceeds.

## Version compatibility trap

Current docs at `docs.opencode.ai/docs` describe a configuration with singular `agent`, `permission`, and TypeScript `@opencode-ai/plugin` tools. Separate OpenCode V2 pages describe `agents` and `permissions` with a different structure. Do not concatenate their snippets. The sample herein targets the current `/docs` style; **its compatibility must be verified against the actual installed pinned binary and SDK version**. If it fails, adapt only the OpenCode binding layer, not the oracle protocol.

## Pilot before next EU IV run (mandatory)

1. Install OpenCode from the official project and pin actual resolved version and `@opencode-ai/sdk` / plugin versions; do not assume a free model is available or meets minimum tool-call reliability.
2. Resolve project-local agent and tools in an isolated worktree. Verify `oracle_inspect`, `oracle_test`, `oracle_diff` appear and are invoked with validated arguments.
3. Test two synthetic native functions: one memory-write mismatch, one ordered-effect mismatch. Agent must revise C++ to pass visible cases then pass protected held-outs.
4. Attempt denied operations: `bash`, external-directory edit, `task` subagent invocation, Git push, Oracle blob modification, malicious path in candidate source ID. Ensure restrictions hold at OpenCode **and sandbox/service layers**.
5. Inject test failure, session abort, malformed tool return, tool timeout, noisy/stalled model, context compaction, and stale SDK protocol. Confirm deterministic receipt and stop.
6. Test a free model, a stronger paid model, and (if useful) a local model with the **same** fixtures and budgets; compare actual tool success and cost.

## Data paths

```text
reconstruction-agent (OpenCode)
  ├─ read / edit candidate worktree
  ├─ oracle_inspect -> trusted local oracle service (read-only evidence)
  ├─ oracle_fixtures -> oracle index and bounded fixture summary
  ├─ oracle_test -> sandboxed compiler/runner -> comparator -> run ID
  ├─ oracle_diff -> bounded failure report + optional context
  └─ oracle_certify -> separate locked held-out suite (no expected bytes returned)

controller (ordinary TS/Python service)
  -> OpenCode session create / prompt / messages / abort
  -> budget / attempt log / selected target / model / stop decision
```

**Single agent does not mean single giant session:** one session per target, with explicit checkpoint summaries and versioned code artifacts. It also does not mean OpenCode's hidden internal summarization has been disabled; those framework internals are not additional project-design agents.

## Source URLs

- https://docs.opencode.ai/docs/custom-tools/
- https://docs.opencode.ai/docs/agents/
- https://docs.opencode.ai/docs/sdk/
- https://docs.opencode.ai/docs/permissions/
- https://docs.opencode.ai/docs/cli/
