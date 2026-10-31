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
- Unresolved among them: 126
- Bounded candidates: 0
- Confirmed graphics dispatches: 0
- Confirmed non-graphics dispatches: 0
- Minimum direct-GL candidates: 30
- Implementation surface (cut plus behind-cut and shared helpers): 97
- GLEW init callers: 2

Counts of indirect sites only. They do not measure shader behavior, resource lifetimes, Metal init, or presentation. 77 unresolved sites record an operand_source slot address. That address is not a closed set of call targets. A slot-load site and a _GfxInit displacement call were checked; their machine operands do not name the callee.

## Platform adaptation

- Platform assessment: `platform_not_assessed`
- Unreferenced NSOpenGL thunks: 7
- Adapter SDL_GL callsites: 12
- CGL rows: 1
- Window scan: scanned
- Window tokens are names found in pseudocode, not a drawable-lifecycle result.
- Window tokens present: SDL_CreateWindow, SDL_GL_SetAttribute, SDL_GL_SetSwapInterval

## Remaining uncertainties

- Material: any survivor listed in `boundary.json` under `bypasses`.
- Platform lifecycle was not assessed. Unreferenced NSOpenGL thunks and `not_observed` CGL stay disclosed.
- Not analyzed: indirect sites in `CInGameIdler::Render` and other functions outside the selected site list.

## Effort estimate

Minimum direct-GL set: 30 functions and 3440 instructions. Decompiled lines for that set, where the snapshot has a body: 1896 across 30 functions. The implementation surface is 97 functions (the `_Gfx*` cut plus behind-cut and shared helpers) and is not classified as retain or replace. Faithful C++ and Metal infrastructure are not estimated. Shader conversion is deferred to stage D.

## Recommendation

`compact_gfx_boundary`
