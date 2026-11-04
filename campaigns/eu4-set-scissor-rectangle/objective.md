Investigate and, only after the checker captures your research note, implement `_GfxSetScissorRectangle`.

Use `python3 -m reverse_engineering.research` and `python3 -m reverse_engineering.ghidra_query`. The corpus is the `EU4_RE_EXPORT` already in this environment. Do not copy it, rebuild it, import a Ghidra project, or reanalyze one. Do not launch EU4. Do not read `analysis/reconstruction/gfx-semantic/`.

Write `analysis/automation/pilot-gfx-set-scissor-rectangle/note.md` before you edit any replacement. A note the checker can capture must contain the resolved symbol and the entry address. Record the body bounds, the patchable prefix, the calling convention, the rectangle and coordinate convention, the memory effects, the OpenGL effect, and what a later Metal render encoder would have to consume. Separate observed instructions, Ghidra hypotheses, and deliberate Metal safety policy. State unresolved assumptions. Do not edit replacement code in this stage.

The checker then stores that note. Leave the stored note alone. Later corrections go under a heading `## Follow-up` after the captured text. A line that starts with `BLOCKER:` stops the campaign. Use it when a faithful effect still needs a render encoder or another dependency this campaign must not add. Do not implement that dependency. A CPU-side rectangle copy presented as a finished Metal scissor operation is not acceptable. Deferred state is acceptable only when the tests show the transitions and name the encoder-facing consumer.

After the checker reports that the research checkpoint was captured, implement the function in these files only:

- `benchmark/eu4_gfx_init.h`
- `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_gfx_entries.def`
- `tests/gfx_init_host.mm`
- `tests/test_gfx_init.py`
- `tests/test_gfx_entry_registry.py`
- `progress.md`

Recover the behavior from the instructions. Add the `GFX_ENTRY` only after the replacement and its tests agree. The prefix must be the original bytes and must end on an instruction boundary. Call the replacement from `tests/gfx_init_host.mm` with a real Metal device.

You may also edit `progress.md` during research. Do not edit any other file. Do not commit, reset, or push. Do not modify or delete the checker, the campaign state, or the research checkpoint. This worktree is not a sandbox: shell commands run as the same OS user as the runner.
