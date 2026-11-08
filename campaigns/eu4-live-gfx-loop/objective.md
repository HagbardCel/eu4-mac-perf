Diagnose and repair a controlled `_GfxSetViewPort` discrepancy using the
authentic reference capture already in this worktree.

The reference is `analysis/gfx-ops/v0/capture/`. It was captured from the
pinned original EU4 executable and records seven viewport invocations, their
six-word input images, and the resulting original master state. Read
`analysis/gfx-ops/v0/note.md` and `analysis/gfx-ops/v0/comparison-contract.md`
first.

Do not launch EU4 in this campaign. The capture is already authentic; the
experiment is whether you can use it offline.

1. Run the differential replay:

   ```
   python3 -m automation.live_gfx replay \
     --capture analysis/gfx-ops/v0/capture \
     --output .local/gfx-diffs/baseline
   ```

2. Read `comparison.json` and `report.md`. Identify the exact `call_id`,
   `field`, original value, and Metal value. Inspect `benchmark/eu4_gfx_init.mm`
   and the original capture record. The deliberate discrepancy is in the
   current `_GfxSetViewPort` replacement, not in the capture.

3. Fix the implementation. Do not edit the capture, the comparison contract,
   the checker, the campaign state, or the Metal shadow worker. Keep the fix
   within the declared CPU-state scope; do not claim GPU or pixel equivalence.

4. Rerun the replay into a new output directory until it returns `pass`. Then
   run:

   ```
   python3 -m unittest tests.test_gfx_observe tests.test_gfx_op_diff \
     tests.test_gfx_startup_diff tests.test_gfx_startup_diff_metal
   ```

5. Write `analysis/automation/pilot-gfx-set-viewport-repair/note.md` with:
   - the replay status before and after the fix;
   - the exact diagnostic fields and values;
   - the original instruction/callsite evidence you used;
   - the fix and why it preserves the declared scope;
   - the test and replay results, including coverage.

Keep `progress.md` current. If the replay returns `unavailable` or
`incomplete`, stop and report it with the existing `BLOCKER:` line; do not
modify Metal blindly. Do not commit, reset, or push.
