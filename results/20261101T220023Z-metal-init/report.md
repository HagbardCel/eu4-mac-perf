# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

The process exited 2 on its own. The launcher did not send a signal. Init thread and the `after_init` stop thread are both `861039`. `init.json` records success 1. `live_gfx_init` is `returned`.

The first unmigrated OpenGL dependency is `glGetIntegerv` in `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` at `0x1015e9e20`, RVA `0x15e9e20`. The caller is `GetOpenGLContextRasterizerState`, after `_GfxInit` returns and outside the six replaced entries.
