# EU4 RE2 offline pilot

One OpenCode session repairs a defective [`_GfxSetConstantBuffer`](../../analysis/reconstruction/_GfxSetConstantBuffer/contract.md) reconstruction in a detached worktree. The checker compiles that candidate and compares it with the frozen 414-scenario reference. This does not add Metal behavior.

Prepare a disposable worktree at the commit named in [campaign.json](campaign.json). Do not use the primary checkout.

```bash
git fetch origin
git worktree add --detach \
  .local/automation/eu4-re2-pilot/workspace \
  2923c1407371656fd51d962b085eaebc52650993
python3 campaigns/eu4-re2-pilot/prepare.py \
  .local/automation/eu4-re2-pilot/workspace
python3 -m automation.cli run campaigns/eu4-re2-pilot/campaign.json
```

`prepare.py` applies the signed-division near miss on the buffer-length path. It refuses a dirty tree or a missing anchor. The runner does not call it, and it never cleans, commits, or pushes the worktree.

The reference is Ghidra P-code execution of the original pinned instructions under constructed state. A match is not proof for every original game state and is not a Metal result. It does not move Decision 0001, 0002, or 0003, or the D1 gates. The checker is a trusted local verifier, not an OS sandbox. The agent process has the same OS privileges as the runner.

An isolated worktree has no default Ghidra index. `EU4_RE_EXPORT` may point at the operator's existing trusted corpus. The pilot does not copy, rebuild, or modify it. Search can fail for lack of an index; the tracked contract and differential reference are still sufficient. See the [reconstruction notes](../../analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/README.md), the [differential notes](../../analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md), and the [semantic architecture](../../analysis/reconstruction/gfx-semantic/v1/architecture.md).

## Pilot

`integration: verified`

Before any agent turn, the detached worktree at `2923c1407371656fd51d962b085eaebc52650993` contained the signed-division defect. The checker returned `fail` (exit 1): 49 scenarios differ, starting with `d019` (`word_count` `0x20000000` against the candidate's `0xe0000000`).

OpenCode 2.2.5 served `opencode/big-pickle` on `127.0.0.1:4096`. The first `automation.cli run` paused `runtime_unavailable` after 0.6s because that server was not reachable. The continued run created session `ses_e5c940906ffePm952HZ6jGfw7W` and finished `complete`. The candidate was not repaired by hand.

- rounds: 1
- tool calls: 20, with 0 tool failures
- validations: 1, of which 1 passed
- active time: 103.5s, including the 0.6s unreachable start
- tokens: input 45426, output 3207, reasoning 2084, cost 0
- files: `gfx_set_constant_buffer.hpp` and `progress.md`
- verdict: `pass`, "reference validated and candidate matched"

The agent restored `return (first_dword + 3u) >> 2;` on the buffer path. No other tracked source differs from the pinned commit. `progress.md` is the only remaining worktree change. The agent reported that no Ghidra index was available and used the tracked contract instead.

A passing verdict is still Ghidra P-code under constructed state. It is not a Metal result and does not move Decision 0001, 0002, 0003, or the D1 gates.
