Investigate and repair the defective `_GfxSetConstantBuffer` C++ reconstruction in this worktree.

Preserve its established ABI and object layout. Use the documented machine-level contract and the existing offline evidence. Implement a semantic correction, not a test-specific workaround. Keep `progress.md` updated. An external check decides when the repair is accepted.

The original function identity is RVA `0x15ec36a`, pinned by the executable SHA-256 in `reverse_engineering/target.json`.

Read these tracked documents. They are evidence. Do not modify them:

- `analysis/reconstruction/_GfxSetConstantBuffer/contract.md`
- `analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/README.md`
- `analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md`
- `analysis/reconstruction/gfx-semantic/v1/architecture.md`

These search commands read the local Ghidra-derived corpus:

```bash
python3 -m reverse_engineering.search symbol _GfxSetConstantBuffer
python3 -m reverse_engineering.search function 0x15ec36a
python3 -m reverse_engineering.search callers 0x15ec36a
python3 -m reverse_engineering.search callees 0x15ec36a
```

An isolated worktree has no default Ghidra index. If `EU4_RE_EXPORT` points at a trusted corpus, those commands use it. Do not copy, rebuild, or modify that corpus. A missing index is not a reason to stop: the tracked contract and the frozen differential reference are enough to repair the candidate.

Edit only these files:

- `analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.cpp`
- `analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.hpp`
- `progress.md`

Do not edit the original-code reference, the differential corpus, the validator, the harness, or the campaign configuration. Do not commit, reset, or push.
