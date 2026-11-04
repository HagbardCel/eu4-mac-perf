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

The run has not finished. Fill this section from the runner report after it stops. Classify the outcome as one of:

- research blocker: `validation_unavailable` and a validation diagnostic containing `BLOCKER:`
- checker failure: verdict `fail`, or `validation_unavailable` without `BLOCKER:`
- offline pass: verdict `pass`, ready for semantic review, not merged and not runtime-equivalent
