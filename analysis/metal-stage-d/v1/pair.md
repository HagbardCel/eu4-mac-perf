# Stage D — one shader pair

Candidate: `gfx/FX/color.shader`, effect `Color`. Fallback, not compiled: `gfx/FX/DebugLines.shader`.

`Color` is the UI position-and-color effect: one `float4x4` matrix, no samplers, vertex inputs `POSITION` (`float2`) and `PDX_COLOR` (`float4`). The main menu and the in-game UI both submit that kind of draw. `DebugLines` is the fallback if that draw's vertices cannot be closed; it is the same kind of pair with a `float4` position and semantic `TEXCOORD0` instead of `TEXCOORD1`. It is not a normal Venice map draw, so it is not the candidate.

## Source

The installed `.shader` file is Clausewitz HLSL, not the GL compiler input. `BuildGLSL` (`0x15efec7`, `gfx_glsl_builder.cpp` in the snapshot) writes the compiler string:

- `#version 120`
- `#define PDX_OPENGL 1`
- `#define NO_SHADER_TEXTURE_LOD` or the `GL_ARB_shader_texture_lod` extension. This pair does not sample textures. The reconstructed files use the `NO_SHADER_TEXTURE_LOD` branch.
- `#define VERTEX_SHADER` or `#define PIXEL_SHADER`
- the pasted text of `gfx/FX/defines_glsl.fxh`
- `uniform vec4 UNIFORM0[4]` and `#define Matrix mat4(UNIFORM0[0], UNIFORM0[1], UNIFORM0[2], UNIFORM0[3])`
- `attribute` / `varying` names `in_POSITION`, `in_PDX_COLOR`, `v_TEXCOORD1`
- `main` renamed to `glsl_main`, and the pixel `: PDX_COLOR` removed

That reconstruction is the snapshot's format strings plus `color.shader`. It is not byte-identical to the compiler input captured later from EU4 (`ea46e32:results/20261031T144434Z-metal-draw/matched.glsl`). The reconstructed vertex file starts:

```glsl
#define NO_SHADER_TEXTURE_LOD
```

The captured vertex source starts:

```glsl
#extension GL_ARB_shader_texture_lod : enable
#define MAP_SIZE_X 5632.000
#define MAP_SIZE_Y 2048.000
#define MAP_POW2_X 1.000
#define MAP_POW2_Y 1.000
#define FOW_POW2_X 1.000
#define FOW_POW2_Y 1.000
```

Color's position and color math uses none of those defines, so the translation was not repeated. `glslangValidator` accepts the reconstructed text as desktop GLSL 120 with no SPIR-V (`color.vert`, `color.frag`).

## Toolchain

OpenGL SPIR-V (`glslangValidator -G100`) rejects `#version 120`:

```text
ERROR: #version: Desktop shaders for OpenGL SPIR-V require version 330 or higher
```

Vulkan semantics were not used. The OpenGL client was kept. For this pair only, the source was normalized, not rewritten by a converter:

- `#version 450` (330 still rejects `layout(location)` on a uniform)
- `attribute` / `varying` / `gl_FragColor` become `in` / `out` / `frag_color`
- `#define texture2D texture` and the matching `texture2DLod` / `textureCube` macros, so the pasted desktop header typechecks
- explicit locations: uniform 0, position 0, color 1, interpolator 0

Tools, from this directory: glslang 16.6.0 (`glslangValidator --version`, Glslang Version 11:16.6.0) and SPIRV-Cross 1.4.363.0.

```text
glslangValidator -S vert color.vert
glslangValidator -S frag color.frag
glslangValidator -S vert -G100 -o color330.vert.spv color330.vert
glslangValidator -S frag -G100 -o color330.frag.spv color330.frag
spirv-cross --msl color330.vert.spv --output color.vert.metal
spirv-cross --msl color330.frag.spv --output color.frag.metal
```

The `#version 120` rejection is `color.spirv120.txt`. The successful OpenGL SPIR-V logs are `color330.vert.glslang.txt` and `color330.frag.glslang.txt`.

The vertex MSL did not compile until one address-space edit: `glsl_main`'s uniform argument is `constant`, matching the buffer it is passed. The fragment library compiled as SPIRV-Cross emitted it. `compile_msl` (`newLibraryWithSource`) reports entry point `main0` for both. The `metal` CLI is not in the active Command Line Tools install. This is a Metal library, not a render pipeline. Vertex descriptors and the render target are the draw milestone.

## Interface

- Vertex attributes: `in_POSITION` `float2` location 0, `in_PDX_COLOR` `float4` location 1. MSL `[[attribute(0)]]` and `[[attribute(1)]]`.
- Uniform: `UNIFORM0`, four `float4`s, buffer 0. The matrix is `float4x4(UNIFORM0[0..3])`.
- Samplers: none.
- Stage I/O: `v_TEXCOORD1` as the color interpolator, location 0. Fragment color output is `frag_color` at color 0.
- `BuildGLSL` can stay off the Gfx cut. A backend can load this MSL and not call it.
