# Gfx boundary census

**Boundary verdict:** `compact_gfx_boundary`

Within recovered static edges from the selected roots. Higher-level indirect calls, including those in CInGameIdler::Render, were not classified. This is not proof that no out-of-cut dispatch exists.

## Direct API census

- Direct GL callers: 32 (25 inside the 92 `_Gfx*` functions)
- `_Gfx*` instructions / bytes: 4415 / 16324
- Outside-Gfx direct GL callers, from call edges:
  - `0x00000000015d25ee` _glewGetExtension (27 instructions): _glGetString
  - `0x00000000015d2712` _glewContextInit (14935 instructions): _glGetString
  - `0x00000000015e9dfa` GetOpenGLContextRasterizerState (42 instructions): _glGetBooleanv, _glGetIntegerv
  - `0x00000000015e9e8e` GetOpenGLContextDepthStencilState (65 instructions): _glGetBooleanv, _glGetIntegerv
  - `0x00000000015e9f78` GetOpenGLContextBlendState (96 instructions): _glGetBooleanv, _glGetIntegerv
  - `0x00000000015ecce2` InternalSetTextureAndSamplerState (84 instructions): _glBindTexture, _glTexEnvf, _glTexParameterf, _glTexParameteri
  - `0x00000000015efec7` BuildGLSL (249 instructions): _glGetString

## Boundary assessment

- Established or uncertain survivors after the cut: 0
- Helpers labeled from the selected roots: 6
- `behind_cut`: 4
- `shared`: 1
- `unknown`: 1
- Incoming edges from outside the cut and outside `behind_cut`: 0
- `unrooted_in_selected_graph`: 3
  - `0x00000000015d25ee` _glewGetExtension
  - `0x00000000015d2712` _glewContextInit
  - `0x00000000015efec7` BuildGLSL
- `shared` `0x00000000015c5a96` GfxCreateTexture
- `unknown` `0x00000000015d2708` _glewInit

Adapter callsites are not counted as structural bypasses.

## Implementation dependencies

- Selected indirect sites: 126
- Unresolved among them: 49
- Bounded candidates: 77
- Confirmed graphics dispatches: 0
- Confirmed non-graphics dispatches: 0
- GLEW init callers: 2

Counts of indirect sites only. They do not measure shader behavior, resource lifetimes, Metal init, or presentation.

## Platform adaptation

- Unreferenced NSOpenGL thunks: 7
- Adapter SDL_GL callsites: 12
- CGL rows: 1
- Window scan: scanned
- Window tokens present: SDL_CreateWindow, SDL_GL_SetAttribute, SDL_GL_SetSwapInterval

## Remaining uncertainties

- Material: any survivor listed in `boundary.json` under `bypasses`, and any `unresolved_active_platform_op`.
- Low impact: unreferenced NSOpenGL thunks, and `not_observed` CGL, unless a required operation is later tied to them.
- Not analyzed: indirect sites in `CInGameIdler::Render` and other functions outside the selected site list.

## Effort estimate

Replace candidates: 29 functions, 3191 original instructions, 1731 decompiled lines. Faithful C++ is estimated at 1731–2596 lines: the low end is the decompiled line count of those functions, and the high end adds about half again for types and calls the decompiler left implicit. That band is taken from the decompiled bodies of the representative functions below, not from an instruction-to-line multiplier. Metal infrastructure, which has no original body, is a separate 1500–4000 lines for a device, queue, pipeline cache, and resource table. Shader conversion is deferred to stage D.
- `_GfxSetViewPort`: 13 instructions, 18 decompiled lines
- `_GfxSetRasterizerState`: 65 instructions, 52 decompiled lines
- `_GfxInit`: 770 instructions, 387 decompiled lines

## Recommendation

`compact_gfx_boundary`
