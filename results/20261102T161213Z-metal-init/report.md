# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 41936. Init thread and create thread are both `2238505`. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`. No `stop.json` and no draw receipt were written. The launcher reached its 180-second deadline, signaled the process, and the exit code is -9. `metadata.json` status is `deadline`.

About 15 seconds after init, with no stop, `sample` ran for 3 seconds. Process CPU time moved from `0:04.06` to `0:04.20`. All 1261 main-thread samples are in `AbortHandler`, blocked in `boost::process::child::wait` on the crash reporter. The frames above the signal are `CEU4Application::InitInMainThread`, `CEU3Graphics::LoadLoadingScreens`, `AddSimpleEffect`, `CEffectHandler::AddGfxEffect`, and `CreateEffectFromFileNew` at RVA `0x1511e8d`. The sampled callee is address 0. That is all the sample establishes.

The binary, not the sample, names the call. RVA `0x1511e8d` is the instruction after `call _GfxCreateShader` at `0x1511e88`. Inside `_GfxCreateShader` (`0x15eae97`), `leaq ___glewCreateProgramObjectARB(%rip)` at `0x15eaeb2` loads the address of the qword at RVA `0x22531f0`, and `callq *(%rax)` at `0x15eaeb9` calls that qword. The sampled callee of 0 is that operand.

This is a wait after that null call. It is not texture staging, not an unguarded draw, and not an idle render loop. The next replacement is that shader-creation call, not another texture format.
