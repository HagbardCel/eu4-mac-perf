# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 47765. Init thread and create thread are both `2319385`. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`. No `stop.json` and no draw receipt were written. The launcher reached its 180-second deadline, signaled the process, and the exit code is -9. `metadata.json` status is `deadline`. Classification is `timeout`, `d1: not_started`.

The game crash file `crashes/eu4_20261102_181249/exception.txt` records the process start. These lines are the observed exception:

```
Date/Time: 2026-11-02 18:12:49

Caught signal 11 (SIGSEGV)

Original Stack Trace:
  2   ???                                 0x0000000000000000 0x0 + 0
  3   eu4                                 0x00000001015eb3e5 GfxBuildAndAddVertexShader + 147
  4   eu4                                 0x0000000101511eba _Z23CreateEffectFromFileNewP19GfxMasterContextGFXPKcS2_PK25SVertexElementDescriptionP10SGfxEffectRK6CArrayI7CStringE + 702
```

That is `SIGSEGV`, callee address 0, then `_GfxBuildAndAddVertexShader` at `0x15eb3e5` and `CreateEffectFromFileNew` at `0x1511eba`. The `glGetString(GL_VENDOR)` stop at `0x15eff24` did not recur.

The exception does not name the instruction inside `_GfxAddVertexShader`. The binary does. `0x15eb3e5` is the instruction after `call _GfxAddVertexShader` at `0x15eb3e0`, on the path taken when `BuildGLSL` returns success. With the shader dword at `+4` equal to 0, that function skips `___glewDetachObjectARB` and reaches `callq *(%rax)` at RVA `0x15eaffa`. The `lea` at `0x15eafee` addresses `___glewCreateShaderObjectARB`, and `edi` is `0x8B31` (`GL_VERTEX_SHADER`).

This is a wait after that null call. It is not a draw.
