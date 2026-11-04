# `_GfxUpdateVertexBufferMultiple` campaign

One OpenCode session investigates `_GfxUpdateVertexBufferMultiple` without a semantic dossier, then implements it against the existing Metal vertex-buffer registry. The detached worktree stays at the commit in [campaign.json](campaign.json). This checkout holds the campaign files. The worktree does not.

`verdict: blocker` is not a runner verdict. [automation/runner.py](../../automation/runner.py) treats it as a failed accept and pauses with `validation_unavailable`. The validation event keeps the diagnostic. That diagnostic contains `BLOCKER:`. This campaign does not add a runner state. Read the validation event, not only `pause_reason`.

A `pass` means automated offline validation succeeded and the change is ready for independent semantic review. It does not authorize a merge and it does not claim runtime equivalence or a completed draw. The worktree is not a filesystem sandbox. OpenCode shell commands run as the same OS user as the runner.

## Prepare

```bash
git fetch origin
git worktree add --detach \
  .local/automation/eu4-vertex-buffer-multiple/workspace \
  97ffb65d0e3f1e14332d7a53c84f9fa6f1155d41
```

The OpenCode server process needs `EU4_RE_EXPORT`, `GHIDRA_INSTALL=/Users/fabian/ghidra_12.2_PUBLIC`, JDK 25 on `PATH`, and `GHIDRA_HEADLESS_MAXMEM=4G`. Export them before `opencode serve --hostname 127.0.0.1`. A query from the runner shell is not the same process.

```bash
python3 -m automation.cli run campaigns/eu4-vertex-buffer-multiple/campaign.json
python3 -m automation.cli resume campaigns/eu4-vertex-buffer-multiple/campaign.json
python3 -m automation.cli status campaigns/eu4-vertex-buffer-multiple/campaign.json
python3 -m automation.cli report campaigns/eu4-vertex-buffer-multiple/campaign.json
```

Do not commit inside the pinned worktree while the runner is active. Do not launch EU4.

## Checker

The first qualifying research note is linked into `.local/automation/eu4-vertex-buffer-multiple/research-checkpoint.md`. That file is written once. Its SHA-256 is the hash of the bytes read back from it. The live note must still begin with those bytes. Corrections belong under `## Follow-up`. A `BLOCKER:` line, before or after that capture, returns `verdict: blocker` and does not authorize implementation.

Later rounds check the checkpoint, the diff scope, the full `GFX_ENTRY` tuple against `research`, and `tests.test_gfx_buffers` plus `tests.test_gfx_entry_registry` run with the candidate worktree as the working directory. Agent-authored assertions are not treated as proof of the original semantics.

## Results

Outcome: **offline pass**. Verdict `pass` means the implementation passed automated offline validation and is ready for independent semantic review. It does not authorize a merge and it does not establish runtime equivalence or a completed draw. The detached worktree stayed at `97ffb65d0e3f1e14332d7a53c84f9fa6f1155d41`. It is not a filesystem sandbox.

Session `ses_e57fdd134ffeGcNsn4zOoQmeQo`, model `opencode/big-pickle`. A separate preflight session decompiled `0x15ec28e` and is not included in these counts.

- OpenCode turns: 3. Validation rounds: 3 (`inconclusive`, `fail`, `pass`).
- Research CLI invocations: 8 (`status` 1, `resolve` 1, `function` 5, `--help` 1). `ghidra_query`: 2 (`function` 1, `address` 1).
- Tool calls: 128 (shell 97, edit 21, read 7, write 3). Tool failures: 0.
- Runner time: 860 seconds. Tokens: input 159333, output 25841, reasoning 32251. Reported cost: 0.
- Production lines: `eu4_gfx_buffers.h` +5, `eu4_gfx_buffers.mm` +78/−4, `eu4_gfx_entries.def` +1. Test lines: `gfx_buffers_host.mm` +305/−2, `test_gfx_entry_registry.py` +2. The captured note is 230 lines; the follow-up adds 26.
- The checker rejected the first registration because its name was `update_vertex_buffer_multiple` rather than `_GfxUpdateVertexBufferMultiple`. The body also does not read the caller's `R9D` or stack dword, so those are not parameters.
- No one edited the research note or the replacement during the run. One runner process was started from the worktree directory and exited before it loaded the campaign, so it wrote no state.
- Unresolved: the draw path does not consume the deferred attribute list here, and this function does not show whether a later draw reads those indices or only `[master+0x170]`. The GLEW slot targets are Ghidra hypotheses. No EU4 process was launched.
- The host tests, run from the candidate worktree, cover two ranges, a zero-size range that leaves later bytes unchanged, in-place update then release and destroy, and stops for `missing`, `range`, `size`, `bounds`, `null_data`, `released`, `attrib`, and `thread`. Those assertions are the agent's. They are not independent proof of the original semantics.

An attribute failure is checked before any byte is copied, so the `MTLBuffer` stays unchanged. The original issues `glBufferSubDataARB` before the attribute loop. That order is a Metal safety policy recorded in the note, and it needs review.
