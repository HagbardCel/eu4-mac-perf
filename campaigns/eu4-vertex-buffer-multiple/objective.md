Investigate and, only after the checker captures your research note, implement `_GfxUpdateVertexBufferMultiple`.

Use `python3 -m reverse_engineering.research` and `python3 -m reverse_engineering.ghidra_query`. The corpus is the `EU4_RE_EXPORT` already in this environment. Do not copy it, rebuild it, import a Ghidra project, or reanalyze one. Do not launch EU4. Do not read `analysis/reconstruction/gfx-semantic/`.

Write `analysis/automation/pilot-gfx-update-vertex-buffer-multiple/note.md` before you edit any replacement. Use these headings:

- `## Entry`
- `## Calling convention`
- `## Memory`
- `## Range layout`
- `## Ownership`
- `## Indirect calls`
- `## OpenGL`
- `## Failure`
- `## Evidence classes`

`## Entry` must cite the entry RVA from `research resolve`. The other sections except `## Evidence classes` must cite instruction RVAs for the claims they make. `## Evidence classes` must separate observed machine instructions, Ghidra hypotheses, and deliberate Metal safety policy, and it must use the words `instruction`, `Ghidra`, and `safety`. Record the entry, the body bounds, the patchable prefix, the calling convention and parameter roles, memory reads and writes, branch conditions, the range-record layout and its offsets, buffer ownership and the engine header, indirect callsites and their possible targets, OpenGL effects and the Metal behavior that stands in for them, and failure behavior. State unresolved assumptions. Do not edit replacement code in this stage.

The checker then stores that note. Leave the stored note alone. Later corrections go under a heading `## Follow-up` after the captured text. A line that starts with `BLOCKER:` stops the campaign. Use it when an essential effect still needs a shader compiler, a command encoder, an OpenGL compatibility layer, a new GPU registry, or another dependency this campaign must not add. Do not implement that dependency.

After the checker reports that the research checkpoint was captured, implement the function in the existing Metal vertex-buffer files:

- `benchmark/eu4_gfx_buffers.h`
- `benchmark/eu4_gfx_buffers.mm`
- `benchmark/eu4_gfx_entries.def`
- `tests/gfx_buffers_host.mm`
- `tests/test_gfx_buffers.py`
- `tests/test_gfx_entry_registry.py`
- `progress.md`

Reuse the existing engine-header lookup, `MTLBuffer` ownership, buffer update and replacement, thread check, backend release, error reporting, and entry patching. Derive the multi-range behavior from the instructions. Do not assume it matches the existing single-range update. Keep engine-visible memory effects. Implement shader-attribute effects, or record them as deferred Metal state and name the future consumer. Add the `GFX_ENTRY` only after the replacement and its tests agree. The prefix must be the original bytes and must end on an instruction boundary.

In `tests/gfx_buffers_host.mm`, call the replacement with real Metal buffers. Include these labels in that new test: `case:multiple-range`, `case:multiple-unchanged`, `case:multiple-lifecycle`, and `case:multiple-error`. They must cover update ranges, bytes that stay unchanged, lifecycle, and the error cases your note supports.

You may also edit `progress.md` during research. Do not edit any other file. Do not commit, reset, or push. Do not modify or delete the checker, the campaign state, or the research checkpoint. This worktree is not a sandbox: shell commands run as the same OS user as the runner.
