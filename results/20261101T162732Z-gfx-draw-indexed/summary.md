# Live `_GfxDrawIndexed` observation

- Outcome: **observed**
- Session: `gfx-draw-20261101T162732Z`
- Site: `0x1015eaa77` (RVA `0x15eaa77`, image base `0x100000000`)
- Executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- Calls from install: 3,077,025
- Calls after Venice readiness: 4,931
- Complete samples: 64
- Read refusals: 0
- Save verified against the Venice fixture: yes

Every retained sample had `rsi`, `rdx`, and `rcx` equal to 0, mode 4, a readable context, and a nonzero uniform byte at `+0x128`. The attribute byte at `+0x170` was nonzero on 57 samples. Selected counts, derived from `+0x68` because `esi` was 0, ran from 84 to 1,239.

This is not a Metal draw and not a buffer capture. Findings: [gfx-draw-indexed-live.md](../../analysis/metal-draw/gfx-draw-indexed-live.md).
