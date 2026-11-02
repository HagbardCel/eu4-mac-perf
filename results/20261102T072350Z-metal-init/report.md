# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 5979. The process exited 2 on its own. The launcher did not send a signal. Init thread and the `after_init` stop thread are both `1533453`. `init.json` records success 1. `live_gfx_init` is `returned`. `metadata.json` status is `complete`.

The stop is `glViewport` at `0x10149b21f`, RVA `0x149b21f`. That address is the instruction after `call _GfxSetViewPort` at `0x149b21a` in `CGraphics::InitGfx`. `_GfxSetViewPort` (`0x15ea4f8`) tail-jumps at `0x15ea527` to the `_glViewport` thunk at `0x18c26ae`, so the return address seen inside `glViewport` is the InitGfx callsite. The historical pc-0 fault at `0x15ebd02` did not recur.

The create return is indirect. `InitGfx` calls `CGraphics::OnResetDevice` at `0x149b082` and does not branch over that call on the way to `0x149b21a`. `OnResetDevice` has no branch; its last call, at `0x149b49d`, is `CPdxParticleObject::OnResetDevice`, and the next instructions are the epilogue and `ret`. That function calls `_GfxCreateVertexBuffer` at `0x14d44df` when a count dword is non-zero, which is the callsite of the previous fault, and it can skip that call when the dword is zero. This session did not record the dword. What it shows is that `OnResetDevice` returned and the next stop is this `glViewport` callsite.

The next dependency is `_GfxSetViewPort`, observed as that `glViewport` tail call.
