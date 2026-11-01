# Offline draw-candidate assessment

No retained record passes. The closed-corpus search ends here. This note does not authorize another capture, and it does not start a Metal draw.

A candidate passes only when every required input is retained and unambiguously associated with the same original draw. Multiple files are acceptable when that common draw identity is established independently. A shared session directory, hash, program id, timestamp, or reconstructed source is not that identity.

Required inputs are both original compiler stages, the vertex bytes and any index bytes with their attribute layout, every active uniform value, and the fixed-function state a replay would set. Also required: every pixel-affecting texture or sampler, or positive evidence that the draw uses none, and the initial framebuffer contents, attachments, and framebuffer state. A format name is not that condition.

Three shader facts stay separate. Raw byte equality with [`analysis/metal-stage-d/v1/color.vert`](../metal-stage-d/v1/color.vert) is false for every retained blob. The prior B2 matcher establishes a supported normalization match for the historical vertex source ([session note](b2/color-note.md)). It does not retain a complete original shader pair, establish same-draw provenance, or supply the missing replay inputs. Only that provenance, together with the other inputs, could pass.

`matched.glsl` is a session-level compiler dump. The producer at [`ea46e32` `benchmark/eu4_metal_draw.m`](https://github.com/HagbardCel/eu4-mac-perf/blob/ea46e32/benchmark/eu4_metal_draw.m) writes it when one `glShaderSource` blob contains both `float2 vPosition` and `in_PDX_COLOR`, using `fopen` with `"w"`. A later match overwrites the file. The copy of `benchmark/eu4_metal_draw.m` on this pull request does not contain that dump. The historical file shows that a matching source was submitted during the session. It does not name the draw that used those bytes, and it is not the fragment stage.

## Measurements

[`color.vert`](../metal-stage-d/v1/color.vert) on this branch is 4,845 bytes, SHA-256 `39f57ee42ec21b60f85cce6989ab6e49a391fd47`. The same length is on `main`. Raw equality with the historical compiler string is false.

`ea46e32` is not reachable from `main`. Its `results/20261031T144434Z-metal-draw/` tree is `gl_flushes`, `hooks.log`, `matched.glsl`, `metadata.json`, and `result.json`. That `matched.glsl` is 5,048 bytes and differs from `color.vert` at byte 36. The diff replaces `#extension GL_ARB_shader_texture_lod` and the map-size defines with `#define NO_SHADER_TEXTURE_LOD`, and it also moves `glsl_main`.

The operator worktree also has six October 31 directories. All six were readable. They are not in this pull request, and the five earlier than 14:44 are not in `ea46e32`. The 14:44 worktree `matched.glsl` is 5,061 bytes, SHA-256 `6b3b10971c21e978d0b8ada02a2ca83305128253`, and it differs from the `ea46e32` blob at byte 645. The same 5,061-byte blob is in the 14:22, 14:27, 14:32, and 14:37 directories. The 14:14 blob is 4,997 bytes, SHA-256 `d2d0502ce64b47c49568c623ac839b9a1822e565`, with a `float4` position and `v_TEXCOORD0`. None equals `color.vert`. An untracked local `color.vert` of 4,856 bytes was also unequal; it is not the file on this branch.

No run directory contains a fragment compiler file, vertex or index bytes, uniform values, texture pixels, or initial framebuffer contents. The 14:44 `result.json` records 246 nonzero pixels, 300 vertices, stride 12, a byte color, depth off, blend off, cull on, and position bounds. The other five `result.json` files record no closed draw. Those summaries are not a buffer and not full state.

## Corpus

| Corpus | Provenance | Shader pair | Buffers | Uniform values | Full state | Pass |
| --- | --- | --- | --- | --- | --- | --- |
| October 31 Metal-draw runs | Operator worktree directories below, not in this pull request. 14:44's five files are also `ea46e32`, and that compiler blob differs from the worktree file | Partial compiler evidence, not tied to one draw | No retained complete payload | No | Partial | No |
| September 28 draw trace | [`results/20260928T113359Z-draw-trace/`](../../results/20260928T113359Z-draw-trace/), `bf956f52` | No | No | No | Partial | No |
| October 3 intrusive diagnostic | [`results/20261003T210202Z-intrusive-diagnostic/report.json`](../../results/20261003T210202Z-intrusive-diagnostic/report.json), `5c65cf01`; `draw_records` is 0 | No | Hashes and signatures | Hashes | Partial | No |
| Stage D static reconstruction | [`analysis/metal-stage-d/v1/`](../metal-stage-d/v1/) on this branch. [`pair.md`](../metal-stage-d/v1/pair.md) says the GLSL is format strings plus `color.shader`, not a captured `glShaderSource` buffer | Reconstructed, not a captured pair | No | No | No | No |
| October 25 Gfx fixture | [`results/20261025T155406Z-gfx-live-publication/`](../../results/20261025T155406Z-gfx-live-publication/), `6248b00f`. `_GfxSetConstantBuffer` publication; transcripts are not in the checkout | Not draw-linked | Not draw-linked | Not draw-linked | Not draw-linked | No |

[`analysis/frame-model-static.json`](../frame-model-static.json) is a static feature seed of installed `.shader` files. [`analysis/frame-model-backend.json`](../frame-model-backend.json) records that program binds were sampled while shader sources, compile and link calls, and the GLSL feature corpus were not inventoried. Neither file ties compiler bytes, buffers, uniforms, or render-target state to one original draw.

October 31 directories read: `results/20261031T144434Z-metal-draw/`, `results/20261031T143735Z-metal-draw/`, `results/20261031T143211Z-metal-draw/`, `results/20261031T142703Z-metal-draw/`, `results/20261031T142142Z-metal-draw/`, `results/20261031T141455Z-metal-draw/`.

The closest evidence is the 14:44 worktree directory. It is not a qualifying draw. The compiler blob is not linked to one draw, it is not raw-equal to Stage D, and the fragment source, buffer bytes, uniform values, full state, textures, and initial render target were not retained.

The next engineering task is a minimal real `_Gfx*`-to-Metal draw, using the existing synthetic host as the correctness baseline. That draw stays explicitly unverified against authentic EU4 draw inputs until such evidence exists. This search does not continue under the corpus above.
