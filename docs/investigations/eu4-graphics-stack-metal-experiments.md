# EU4 graphics stack — what the Metal sessions showed

These notes are from the coexistence, Color-shader, and first-draw sessions on GOG EU4 v1.37.5.0 (x86_64 under Rosetta). They do not replace the older paused-Venice census in [eu4_macos_rendering_findings.md](../eu4_macos_rendering_findings.md). The shader pair itself is recorded in [pair.md](../../analysis/metal-stage-d/v1/pair.md). The static Gfx cut is [boundary.md](../../analysis/metal-census/v1/boundary.md).

## The process is still an OpenGL app

The shipping binary links OpenGL and GLUT. A Metal device can be created in the same process, and an offscreen Metal command buffer can complete while the game is also presenting with `CGLFlushDrawable`. That is coexistence of two APIs, not a Metal swapchain for the game.

The game window is not a Metal view. `SDL_Metal_CreateView` returned null. A capture of the window was not the GL frame. CoreGraphics can still see an EU4-owned window (one coexist session: empty title, 2048×1152). The GL drawable and that window rectangle are different objects.

## How a frame is presented

`CGraphics::PresentScene` reaches `CGLFlushDrawable`. A return of `kCGLNoError` means that flush presented. It does not mean a particular draw ran, and it does not mean the pixels on screen were inspected. Flushes that return an error are not presents. The game often ignores `SIGTERM`; the sessions that had already observed a flush were ended with `SIGKILL`.

Launch that reaches the map is `./eu4` from `eu4.app/Contents/MacOS`, plus the launcher arguments, and `--continuelastsave` for the Venice 1444.11.11 save. The log line `End RestoreDeviceObjects` is when the in-game device objects exist. Draws continue after that. A flush during the load screen is an earlier present.

## How draw calls are bound

GLEW fills its pointers with `dlsym`. Among the draw entry points, the direct Mach-O import that interposition sees is `glDrawArrays`. `glDrawElements` is taken through `dlsym`, including the ARB alias. The process also resolves `glDrawRangeElements`, `glDrawElementsBaseVertex`, and the instanced ARB names. A hook that only replaces the direct `glDrawArrays` symbol misses the indexed UI and map draws.

Early presents are small. Loading-screen traffic is `GL_QUADS` (mode 7) and four-vertex triangle strips (mode 5), on a handful of programs. After `End RestoreDeviceObjects` the hooked `glDrawArrays` and `glDrawElements` calls are in the millions per minute, spread across a few dozen programs, not thousands of distinct programs.

## Two different uses of `in_PDX_COLOR`

`gfx/FX/color.shader` is the Clausewitz effect `Color`: a `float2` position, a `float4` color, and `uniform vec4 UNIFORM0[4]` packed as the matrix. It declares no sampler. The interpolator is `v_TEXCOORD1`. The installed `.shader` file is not the string passed to the GL compiler. `BuildGLSL` pastes a shared header in front of it. That header defines `float2` as `vec2` and includes unused sampler helpers (`sampler2D` in `tex2Dlod` and the like) even for this effect. The captured compiler input also enables `GL_ARB_shader_texture_lod` and defines `MAP_SIZE_*` and `FOW_POW2_*`. Color's position and color math uses none of those.

Other live programs also contain `in_PDX_COLOR` and do not contain the text `float2 vPosition`. Their vertex structs use `float4 vPosition`. They showed up on the load screen and on the map (including a large indexed draw). Matching Color on the attribute name alone selects those shaders too.

## The one queried Color draw

One session, after the original `glDrawElements` returned, read the current program and accepted it as Color. The draw was program 172, `GL_TRIANGLES`, indexed, 300 vertices, one buffer, stride 12. Position was `GL_FLOAT`, size 2, offset 0. Color was normalized `GL_UNSIGNED_BYTE`, size 4, offset 8. Positions lay in ±24. Depth and blend were off. `GL_CULL_FACE` was on, the cull face was `GL_BACK`, and the front face was `GL_CW`. The offscreen Metal replay of those vertices had 246 pixels that were not the clear color.

That receipt was not kept on the branch tip. Later idle sessions on the same save, including ones that logged on the order of 10⁷ hooked draws and about 39 inspected programs, never saw a current program whose vertex source contained `float2 vPosition`. Color is a real UI submission path. It is not the steady map frame.

## What a backend would have to copy

For this effect, a Metal pipeline is a 2-float position, a 4-byte normalized color, a 64-byte uniform (four `float4`s, one query per element), no depth, no blend, and back-face culling. The winding is not "copy the GLenum." Metal's clip space is Y-up, like GL, and the viewport used here has its origin at the top left with a positive height. Under that viewport, a counterclockwise clip-space triangle survives `MTLWindingCounterClockwise` with back-face culling and disappears under clockwise winding. The EU4 draw's front face was clockwise, so that mapping selects clockwise.

The map frame is a different problem: many programs, indexed draws, and entry points beyond `glDrawElements`. Nothing in these sessions measured those pipelines.
