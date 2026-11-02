# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 20282. The process exited 2 on its own. The launcher did not send a signal. Init thread, create thread, and stop thread are all `1856966`. `init.json` records success 1. `metadata.json` status is `complete`.

`create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, the instruction after the particle call at `0x14d44df`.

The stop is `glGenTextures` at RVA `0x15ec4ed`, the instruction after the call at `0x15ec4e8` in `_GfxInitializeTexture`. The create receipt is the return at `0x14d44e4` in `CPdxParticleObject::OnResetDevice`. After it, that function may call the replaced destroy and a second create, then returns at `0x14d4548`. `CGraphics::OnResetDevice` then runs its epilogue and returns to `InitGfx` at `0x149b087`. From there through `call _GfxSetViewPort` at `0x149b21a`, `InitGfx` calls accessors, settings reads, `UseShadows(bool)`, the logger, and empty `_GfxSetGamma`. The logger virtual at `0x149b17d` is `ctype<char>::do_widen` with argument `0x0a`. This image defines no `ctype` vtable, so that call stays in libc++. The direct calls from that window which land in the image are settings, the logger, `CString`, `ostringstream`, the mutex, and `ostream::flush`. The other destinations are `operator new`, `operator delete`, unwind, `strlen`, and libc++ stream stubs. After the viewport call, the mutex destructor calls empty `_GfxRenderUnlock` and `InitGfx` returns at `0x149b25c`. `_GfxInitializeTexture` is not on that path, so the viewport replacement returned and `InitGfx` returned. The previous stop at `0x149b21f` did not recur.

The next dependency is the `glGenTextures` call at `0x15ec4e8`.
