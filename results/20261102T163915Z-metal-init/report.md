# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 44393. Init thread and create thread are both `2273797`. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`. `stop.json` and `blocked_at.json` both name `glGetString` at RVA `0x15eff24` on that thread. Exit code 2, launcher did not signal. Classification is `blocked_at`. No draw receipt.

`_GfxCreateShader` (`0x15eae97`) returned. The next call from `CreateEffectFromFileNew` is `_GfxBuildAndAddVertexShader`, which calls `BuildGLSL`. At RVA `0x15eff1a` that function loads `edi` with `0x1F00` (`GL_VENDOR`) and calls `glGetString`. The stop address `0x15eff24` is the return from that call. The caller is outside the replaced bodies.

This is the vendor-string query in GLSL construction. It is not the previous null call at `0x15eaeb9`, and it is not a draw.
