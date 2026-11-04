# EU4 index-buffer campaign

One OpenCode session adds the four index-buffer replacements in a detached worktree. The checker compiles the trusted host against that worktree's buffer header and registry. This does not change draw selection, Decisions 0001–0003, or the D1 gates.

The contract is [the index-buffer note](../../analysis/metal-init/index-buffers/note.md). The worktree is pinned to the commit in [campaign.json](campaign.json), which does not contain that note. [objective.md](objective.md) gives the note's absolute path. The agent may edit only `benchmark/eu4_gfx_buffers.mm`, `benchmark/eu4_gfx_buffers.h`, `benchmark/eu4_gfx_entries.def`, and `progress.md`.

Prepare a disposable worktree. Do not use the primary checkout. `prepare.py` only checks the pin and a clean tree.

```bash
git fetch origin
git worktree add --detach \
  .local/automation/eu4-index-buffer/workspace \
  15cbc1861aa99cfb722cc2276171c946cf69ca5f
python3 campaigns/eu4-index-buffer/prepare.py \
  .local/automation/eu4-index-buffer/workspace
python3 -m automation.cli run campaigns/eu4-index-buffer/campaign.json
```

`accept.py` prints `pass`, `fail`, or `unavailable`. A missing index registration or implementation is `fail`. No compiler, or no Metal SDK, is `unavailable`. Linux skips the host. That skip is not a Metal pass.

## Run

`integration: verified` for the index-buffer create. The following draw was not run.

Before any agent turn, the detached worktree at `15cbc1861aa99cfb722cc2276171c946cf69ca5f` had no index-buffer entries. The note was readable at its absolute path. The checker returned `fail` (exit 1): missing registration for the four index functions.

OpenCode 2.2.5 served `opencode/big-pickle` on `127.0.0.1:4096`. Session `ses_e5c5b7343ffehVZTO0WtZmC5u3` implemented the registry. The runner's one validation failed because the trusted entry probe wrote its page again after restore had made that page executable. The candidate was not repaired by hand. The runner was stopped during the second round, at 689s of recorded elapsed time, so that harness fault would not push the candidate around.

- rounds: 1 validation, 0 runner passes
- tool calls: 61, with 1 tool failure (a read of `progress.md` before it existed)
- active time: 689s
- tokens: input 110458, output 16463, reasoning 30516, cost 0
- files: `benchmark/eu4_gfx_buffers.mm`, `benchmark/eu4_gfx_buffers.h`, `benchmark/eu4_gfx_entries.def`, and `progress.md`

The probe now restores write permission before it fills the page again. The same worktree then returned `pass`: the trusted host and the candidate registry. Those three source files are the implementation on this branch.

The live `metal_init` session is [20261103T231348Z](../../results/20261103T231348Z-index-metal-init/report.md). `index_create.json` shows the pinned executable, caller `0x14bfa60`, the graphics thread, and a real buffer of 17844 bytes. The process then faulted in original `CGraphics::BeginScene`, which is outside these replacements. That fault stops the session. It is not `packet_ready` or `draw_executed`. The operator-reported signal, `Date/Time: 2026-11-04 00:14:01`, and the frames through `CGraphics::BeginScene(bool)+39` are quoted in that report.
