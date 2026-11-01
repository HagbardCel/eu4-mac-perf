# metal_init 20261101T233006Z

One process, pid 76944. `init.json` records `success` 1 and that pid on thread 983858. There is no `stop.json`. `game.stdout` is empty. `game.stderr` is system log noise for pid 76944.

LLDB spawned this process and stopped at `_dyld_start` before continuing. Before that continue, breakpoint 1 on `_ZN12_GLOBAL__N_112AbortHandlerEiP9__siginfoPv` resolved in the pinned eu4 image at `0x100153e8c2`, hit count 0. That address matches the file address, so the RVAs below subtract `0x100000000`.

The first fault stop is thread 1, tid `0xf0332` (983858, the init thread):

- stop reason `EXC_BAD_ACCESS (code=1, address=0x0)`
- pc `0x0`
- frame 1 `0x10015ebd02` `GfxCreateVertexBuffer + 79`, RVA `0x15ebd02`
- frame 2 `0x10014d44e4` `CPdxParticleObject::OnResetDevice() + 128`, RVA `0x14d44e4`
- frame 3 `0x100149b4a2` `CGraphics::OnResetDevice() + 118`, RVA `0x149b4a2`
- frame 4 `0x100149b087` `CGraphics::InitGfx(SWindowHandle) + 2147`, RVA `0x149b087`

`0x15ebd02` is outside the eight replaced bodies. The stack is `InitGfx` → `CGraphics::OnResetDevice` → `CPdxParticleObject::OnResetDevice` → `GfxCreateVertexBuffer`, so both state replacements had returned.

A second continue stopped on the same exception. `AbortHandler` was not entered. The process did not `_exit`. The launcher then raised in `stop_game` because the stopped task outlived `SIGKILL` until the debugger detached, so it did not write `metadata.json` itself. That file was written from these same receipts: status `debugger_held`, pid 76944, no exit code.

This is a verified fault in the retained vertex-buffer function. It is not a GL diagnostic, and it is not a fault inside either new replacement.

`results/20261101T224903Z-metal-init/stop.json` is not the game.
