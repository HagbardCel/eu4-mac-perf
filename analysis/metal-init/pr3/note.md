# PR3 Stage A

Pinned Mach-O SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. RVAs are from image base `0x100000000`. Runtime address is the live image base plus the RVA. No ASLR absolute is hardcoded.

## ABIs

`_GfxInit` (`0x15e8ea0`): `rdi` is the window-handle struct, `rsi` is the success byte. The window id passed to `SDL_GetWindowFromID` is the dword at handle `+0`. `SDL_GetWindowSize` fills master `+0xa0` and `+0xa4`. Master `+0xa8` is the dword at handle `+0x18` when that dword is nonzero, otherwise 1. Nothing in this function stores the viewport. `+0xac` for `0x18` bytes stays zero until a later `SetViewPort`. The function returns the `0x198` master whenever `::operator new` succeeded, including the failure paths. The success byte is cleared before the window lookup. The final store is the only one that can set it.

`gMasterContext` (`__ZL14gMasterContext`): the store at `0x15e8fe7` is `4c 89 25 82 f5 c4 00`. Next ip `0x15e8fee` plus disp32 `0xc4f582` is RVA `0x2238570`. Shutdown uses the same symbol. The global is stored before the final success byte, so a failure return can leave it set. This replacement leaves it set to the master it returns, and clears it in `_GfxShutdown`.

Setters (`0x15ea0bf`, `0x15ea261`, `0x15ea35a`): `rdi` is the handle, `rsi` is the state. `movq (%rdi), %r14` loads the master. Offset 0 of the master points at itself, so the loaded value is the master. A null state uses `+0x80`, `+0x88`, or `+0x90`. `_GfxShutdown` (`0x15ea42c`) takes the master in `rdi` and reads `+0x180` directly.

`_GfxGetGraphicsSettings` (`0x15ed388`): `edi`, `esi`, and `edx` are the first three arguments. `rcx` is the settings object. `edx` is the record count. This caller (`CGraphicsSettings::Init`, `0x14a1a80`) passes 15. The array is the pointer already at settings `+8`. The constructor fills it at object `+0xfc` with counts 2, 3, …, 16 and a zero flag byte. The body does not allocate, does not append, and does not write the count back. For each record it writes only byte `+4`. The running threshold starts at 2 and doubles when the record's count is greater than the threshold. The flag is 1 only when the count equals that threshold and the count is within the device limit. Counts 2, 4, 8, and 16 are the ones that can match. The only caller ignores `eax`. A missing device takes that same ignored return (`0` here) and marks every flag 0. Anisotropy at `+0x14` stays 0: Metal has no query that replaces the GL probe. Byte `+0x10` is 1, `+0x18` is `0x30000000300`, and `+0x20` is `0x400` on every path that finishes.

## InitGfx cleanup

Both callsites in `CGraphics::InitGfx` (`0x149ada9`, `0x149ae0a`) call `_GfxInit` and store `rax` at `CGraphics+0x80`. The success byte is a stack slot cleared before the first call. If that byte is still 0 and the settings are fullscreen, it changes the resolution and calls `_GfxInit` again, overwriting the pointer. It does not call `_GfxShutdown` and it does not free the first master. A failed return can be followed by one more call. A call after a successful return is not this path.

So a failure return does not write `init.json`. The successful return writes it once. A call after that success is reentry: leave `init.json`, clear the success byte, write `stop.json` with reason `reentry`, and return the existing master. No second backend.

## Snapshots, format, capability

The setters copy a translated prefix, not the description. Blend copies `0x24` bytes from the state object onto master `+0xc`. Depth copies `0x24` bytes onto `+0x30`. Rasterizer copies `0x10` bytes onto `+0x54`, and a set wireframe byte would then store `0x1b01` at `+0x58`. Init leaves wireframe `+0x194` clear, so that store does not happen. `state.apply` writes those prefixes from the default description. Bytes outside them stay the zeros from the master allocation.

Default blend description stores: word 0 at `+0`, then `01 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00` at `+4`, then `00 00 00 00 00 00 00 00 0f 00 00 00 06 00 00 00` at `+0x14`. Bytes `+2` and `+3` are not stored. The translated prefix is GL one/zero, `GL_FUNC_ADD` (`0x8006`), compare `6+0x200`, and four mask bytes of 1. Depth's translated prefix is the B1 default: enable and write bytes 1, compare `0x203`, stencil compare `0x207`, three `GL_KEEP` (`0x1e00`) ops, both masks `0xffffffff`. Rasterizer's translated prefix is `GL_BACK` (`0x405`), `GL_FILL` (`0x1b02`), `GL_CCW` (`0x900`), scissor 0, cull 1.

Fallback `GfxCreateTexture` at `0x15e99fb` passes format `0xb`, 4×4, type 0, and 64 bytes of `0xff`. Format `0xb` selects internal `GL_RGBA8`, external `GL_BGRA`, type `GL_UNSIGNED_BYTE`, which is `MTLPixelFormatBGRA8Unorm`. The header is `0x44` bytes at `+0x98`. Name `+4` stays 0. One mip, sampleable, not a render target.

`GL_MAX_TEXTURE_SIZE` (`0x0D33`) is stored as the dword at `+0x188`. The other bytes through `+0x192` stay 0. 8192 when the device is Apple2 and not Apple3 or Mac2. 16384 otherwise.

## Entry prefixes

| RVA | Prefix |
| --- | --- |
| `0x15ed388`, `0x15e8ea0`, `0x15ea0bf`, `0x15ea261`, `0x15ea35a` | `55 48 89 e5 41 57` (6) |
| `0x15ea42c` | `55 48 89 e5 53` (5) |

The two CALL sites stay `e8 14 ec ef ff` at `0x13cc1d7` and `e8 27 ef ef ff` at `0x13cc1e4`.

## Not settled

Fourteen indirect `call [rcx+0x38]` log sites. The wireframe alias (`+0x194` versus a pointer that is not shown to be the master). The meaning of the settings immediates. Blend and rasterizer description fields no default reader loads. The GL interpose is the symbols these six bodies call, not every GL entry and not a pointer cached before the interpose is bound. The original init also stores primitive token `4` at master `+8`. This replacement leaves that dword 0.

## Offline result

`python3 tests/test_gfx_init.py` and `python3 tests/test_metal_boot.py` passed on the x86_64 hosts. Neither launches EU4. Live `_GfxInit` integration is not verified.

New files: `eu4_gfx_entry_patch` 349 lines, `eu4_metal_events` 139, `eu4_metal_gl_diag.c` 141. `eu4_gfx_init.mm` is 650 including the existing depth writer. `eu4_metal_boot.m` is 593 including the existing a1 and bootstrap path. Host checks are `tests/gfx_init_host.mm` 361 and `tests/metal_boot_host.c` 446.
