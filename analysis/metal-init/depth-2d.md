# Depth-stencil snapshot

`_GfxSetDepthStencilState` is `[0x15ea261, 0x15ea35a)`. `rdi` is the handle and `(%rdi)` is the master. `rsi` is the state object. A null `rsi` uses the pointer at master `+0x88`. The body compares the object's `0x24`-byte prefix with master `+0x30` and then copies that prefix there. Its GL calls are not reproduced.

`_GfxCreateDepthStencilState` allocates `0x4c` bytes and copies the `0x28`-byte `SDepthStencilDescription` to object `+0x24`. `SOpenGLDepthStencilState::Init` at `0x15edb8c` writes the prefix from that description. The numbers below are those instructions, not the earlier dossier.

`GetOpenGLCompareFunction` at `0x15edd14` maps an engine ordinal. Ordinals `0..7` become `0x200` through `0x207` (`GL_NEVER` through `GL_ALWAYS`). Ordinal `1` is `GL_LESS` (`0x201`). Any other dword becomes `GL_LEQUAL` (`0x203`).

`GetOpenGLStencilOperatorFunction` at `0x15edd2e` maps an engine ordinal through the seven dwords at RVA `0x1a795d0`. The check is unsigned and happens before subtraction. Only ordinals `1..7` are in range:

| Ordinal | GL value |
| --- | --- |
| 1 | `0` (`GL_ZERO`) |
| 2 | `0x1E01` (`GL_REPLACE`) |
| 3 | `0x8507` (`GL_INCR_WRAP`) |
| 4 | `0x8508` (`GL_DECR_WRAP`) |
| 5 | `0x150A` (`GL_INVERT`) |
| 6 | `0x1E02` (`GL_INCR`) |
| 7 | `0x1E03` (`GL_DECR`) |

Ordinal `0` and every ordinal above `7` become `GL_KEEP` (`0x1E00`).

The prefix is:

- Byte `+0`: description byte `+0`, the depth-test enable.
- Byte `+1`: `1` when description dword `+4` is `1`, otherwise `0`.
- Dword `+4`: compare translation of description dword `+8`.
- Byte `+8`: description byte `+0xc`, the stencil-test enable.
- Dword `+0xc`: description dword `+0x10`.
- Dword `+0x10`: description dword `+0x14`.
- Dword `+0x14`: compare translation of description dword `+0x24`.
- Dwords `+0x18`, `+0x1c`, and `+0x20`: stencil-op translation of description dwords `+0x18`, `+0x1c`, and `+0x20`.

`eu4_gfx_write_default_depth_stencil` produces depth enabled and writable, compare `0x203`, stencil disabled, stencil compare `0x207`, and three `GL_KEEP` ops. That is the snapshot previously stored by `fill_depth_snap`. The backend header's prefix is not used: init writes the description and this translation separately.
