# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 39535. Init thread and create thread are both `2189197`. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, the same return as the previous session.

No `stop.json` and no draw receipt were written. The launcher reached its 180-second deadline, signaled the process, and the exit code is -9. `metadata.json` status is `deadline`.

The previous `_GfxInitializeTexture` stop, format 9 at caller RVA `0x15ec663`, did not recur. No later `blocked_at` was recorded, and none of the three hooked `_GfxDraw*` entries ran. That is not a draw.
