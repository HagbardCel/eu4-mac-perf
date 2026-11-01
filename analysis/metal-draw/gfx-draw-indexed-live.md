# `_GfxDrawIndexed` entry observation, 2026-11-01

Session `results/20261101T162732Z-gfx-draw-indexed/` is one read-only observation of the original OpenGL `_GfxDrawIndexed` at RVA `0x15eaa77`. The executable SHA-256 was `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. The loaded image base was `0x100000000`, so the patched site was `0x1015eaa77`. The process used the disposable Venice save. The working-save hash still matched the fixture after the run, and the previous continue file was restored. Outcome is `observed`.

The detour forwarded every call. It did not replace the draw, read GL buffer bytes, or issue a Metal command. Detailed samples started only after Venice was ready. The first 64 complete post-readiness samples were kept. Later calls were counted and not recorded.

| Measurement | Value |
| --- | --- |
| Calls from install through the window | 3,077,025 |
| Calls after Venice was ready | 4,931 |
| Complete samples | 64 |
| Read refusals | 0 |

All 64 samples used the same context slot, `rdi = 0x7ff3476f4720`. `rsi`, `rdx`, and `rcx` were 0, so the incoming count, the unread third argument, and the base vertex were all zero in this window. The mode at context `+0x8` was 4 (`GL_TRIANGLES`) every time. The context image was readable, and the parent pointer at `+0x70` was non-null: `0x7ff2e60fb000` on 62 samples and `0x7ff2e66133e0` on 2. Because `esi` was 0, the count the function keeps is the stored value at `+0x68`. Those counts ran from 84 to 1,239.

The uniform byte at `+0x128` was nonzero on all 64 samples. The attribute byte at `+0x170` was nonzero on 57 and clear on 7. The offline adapter refuses a gather while either byte is set, so it would refuse every sample in this window. A Metal indexed draw has to consume that pending uniform upload, and usually the pending attribute enable, before it can issue `drawIndexedPrimitives`. This window did not show a nonzero base vertex.

The receipt is [`results/20261101T162732Z-gfx-draw-indexed/receipt.txt`](../../results/20261101T162732Z-gfx-draw-indexed/receipt.txt). The raw register and context fields are in [`samples.txt`](../../results/20261101T162732Z-gfx-draw-indexed/samples.txt). The selected counts in the receipt were derived again from `rsi` and `stored_count`; the observer did not store them.
