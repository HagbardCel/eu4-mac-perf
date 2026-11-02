# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 51420. Init thread and create thread are both `2458211`. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`. No `stop.json` and no draw receipt were written. The launcher reached its 180-second deadline, signaled the process, and the exit code is -9. `metadata.json` status is `deadline`. Classification is `timeout`, `d1: not_started`.

The game crash file `crashes/eu4_20261102_204115/exception.txt` records the process start. These lines are the observed exception:

```
Date/Time: 2026-11-02 20:41:15

Caught signal 11 (SIGSEGV)

Original Stack Trace:
  2   ???                                 0x00000003073695d0 0x0 + 13005919696
  3   eu4                                 0x00000001015eb844 GfxBuildAndAddPixelShader + 150
  4   eu4                                 0x0000000101511f1d _Z23CreateEffectFromFileNewP19GfxMasterContextGFXPKcS2_PK25SVertexElementDescriptionP10SGfxEffectRK6CArrayI7CStringE + 801
```

That is `SIGSEGV`. The frame at `0x15eb844` is the instruction after `call _GfxAddPixelShader` at `0x15eb83f`, on the path taken when `BuildGLSL` returns success. `0x1511f1d` is the instruction after `call _GfxBuildAndAddPixelShader`. The vertex-shader failure return continued into pixel-shader construction. The address above that frame, `0x3073695d0`, is outside the EU4 image. It is not address 0.

The exception does not name an instruction inside `_GfxAddPixelShader`. The binary does. The dword at shader `+8` is 0, so the detach is skipped. The next indirect call is `callq *(%rax)` at RVA `0x15eb471`. The `lea` at `0x15eb465` addresses `___glewCreateShaderObjectARB`, and `edi` is `0x8B30` (`GL_FRAGMENT_SHADER`).

This is not a draw.
