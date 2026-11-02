# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 14511. The process exited 2 on its own. The launcher did not send a signal. Init thread, create thread, and stop thread are all `1718011`. `init.json` records success 1. `metadata.json` status is `complete`.

`create.json` records `success` 1 for `_GfxCreateVertexBuffer`. The image is the pinned executable. The return address is `0x1014d44e4`, RVA `0x14d44e4`, the instruction after the particle callsite at `0x14d44df`. That receipt is written before the replacement returns, so it shows the successful creation path was reached.

The stop is `glViewport` at RVA `0x149b21f`, the instruction after `call _GfxSetViewPort` at `0x149b21a` in `CGraphics::InitGfx`. That callsite is after `OnResetDevice` returns. Together with `0x14d44e4`, the historical particle call completed and execution continued. The pc-0 fault at `0x15ebd02` did not recur.

The next dependency is `_GfxSetViewPort`, observed as that `glViewport` tail call.
