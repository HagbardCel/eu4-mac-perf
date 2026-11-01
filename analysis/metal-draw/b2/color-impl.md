# B2 Color implementation

This note is the fixture and preflight contract for the Color capture. The candidate itself stays in [color-contract.md](color-contract.md). [color-note.md](color-note.md) is written only after an authorized session, and only to record that session's outcome.

`GL_DITHER` was not part of the historical measurement. OpenGL leaves it enabled unless the application disables it, so the capture stores the bit and the standalone GL replay applies it. Metal has no dither control. The frozen pixel cap is unchanged either way.

The fragment compiler input from the historical session was not retained. Fragment identity is the reconstructed Stage D source in `analysis/metal-stage-d/v1/color.frag`. That is an eligibility check on the live fragment, not a claim that the historical fragment text was recovered.

Inspection runs after the original `glDrawElements` returns. It assumes that call does not rebind the buffers or replace the program. The capture restores any binding it changes and does not call `glGetError`.

The indexed wrapper is the directly bound `glDrawElements` symbol, and `dlsym` lookups of `glDrawElements` and `glDrawElementsARB` return that same wrapper. It forwards only through this capture image's own `glDrawElements` call. A pointer returned by `dlsym` is not the forward target. `indexed_draws` counts draws while acquisition is active. After the first accepted fixture, later draws still forward and do not increment `indexed_draws`.

## Fixture schema

Schema version 1. `analysis/metal-draw/b2/color_capture.m` writes these files under `fixture/` after the original draw. The replay host checks the version, sizes, offsets, and hashes before it allocates or draws.

| File | Contents |
| --- | --- |
| `fixture.json` | Schema below |
| `vertices.bin` | Original array-buffer bytes |
| `indices.bin` | Original element-buffer bytes |
| `uniform0.bin` | 64 bytes, 16 little-endian `float32`, column order from `glGetUniformfv` |
| `draw.vert.glsl` | Captured vertex compiler input |
| `draw.frag.glsl` | Captured fragment compiler input |

`reference.rgba` and `clear.rgba` are written later by `tests/metal_color_ref_host.m`. They are `width * height * 4` bytes, RGBA, row 0 at the top. `glReadPixels` is bottom-up and is flipped before those files are written and before compare. Metal readback stays BGRA8 and is swizzled into that same top-down RGBA order.

`fixture.json` fields:

- `schema_version`, `capture_version`: both `1`
- `executable_sha256`: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- `program_id`: original GL name, identification only
- `primitive`: `GL_TRIANGLES`
- `index_type`: `GL_UNSIGNED_SHORT` or `GL_UNSIGNED_INT`
- `count`, `index_offset`: index count and byte offset into `indices.bin`
- `vertex_bytes`, `index_bytes`, `stride`: `stride` is 12
- `attributes`: `in_POSITION` then `in_PDX_COLOR`, each with `location`, `shader_type` (`GL_FLOAT_VEC2` or `GL_FLOAT_VEC4`), `size`, `component_type`, `normalized`, `offset`
- `viewport`: `x` and `y` are 0; `width` and `height` are in `1..2048`. A larger target is an acquisition failure, not a reason to raise the cap
- `cull` true, `cull_face` `GL_BACK`, `front_face` `GL_CW`
- `dither`: the captured Boolean
- Fixed false or fixed values: `depth_test`, `blend`, `stencil_test`, `scissor_test`, `alpha_test`, `color_logic_op`, `framebuffer_srgb`, `polygon_smooth`, `clip_planes`, `primitive_restart`, `samples` 0, `color_mask` all true, `polygon_mode` `GL_FILL`
- `clear`: `[16, 32, 48, 255]`
- `gl_format` `RGBA8`, `metal_format` `BGRA8Unorm`, `comparison` `RGBA_top_down`
- `acceptance`: `coverage_mismatches` 0, `max_channel_delta` 1, `synthetic_exact` true
- `hashes`: SHA-256 of the five original-input files

Caps, reject rather than truncate: vertex bytes 1 MiB, index bytes 256 KiB, each shader 128 KiB. Those caps decide whether a loaded fixture may be replayed. The host reads and hashes the files before it applies them, so they do not limit allocation of an untrusted fixture. The replay host applies those caps and walks every index before it draws. For stride 12, position occupies `[base, base+8)` and color `[base+8, base+12)`. A referenced index outside that record is rejected on its own, separately from the index buffer's byte range. After link, the host requires exactly the Color attribute pair and `UNIFORM0[4]`. A nonfinite matrix is a reject. A mapped buffer is a reject before `glGetBufferSubData`. Index ranges use overflow-safe 64-bit arithmetic.

On this legacy driver a draw with a vertex array object still produces no fragments unless vertex attribute array 0 is enabled. The replay enables array 0 from the same position bytes when neither captured location is 0. The linked shader locations remain the captured ones. Array 0 is not a shader input.

Shader identity is the whole token stream of the Stage D source after removing comments, rewriting only the two known preamble blocks to `#define NO_SHADER_TEXTURE_LOD`, and moving the known attribute and varying declarations to the Stage D positions. The historical preamble is `#extension GL_ARB_shader_texture_lod : enable` plus `MAP_SIZE_X 5632.000`, `MAP_SIZE_Y 2048.000`, `MAP_POW2_X 1.000`, `MAP_POW2_Y 1.000`, `FOW_POW2_X 1.000`, and `FOW_POW2_Y 1.000`. Any other token fails. The replay compiles the captured GLSL, not the Stage D text. Metal uses the Stage D MSL pair.

A coverage mismatch is a pixel where one side equals the clear color and the other does not. Acceptance is zero of those, and a maximum absolute channel difference of 1 everywhere else. Synthetic dither-off images must still match exactly. Do not raise the cap after a mismatch.

The original fixture directory is gitignored under `fixtures/`. Do not commit captured EU4 shader or geometry bytes. The historical vertex compiler input stays in commit `ea46e32` and is not copied into this tree.

## Three gates

1. Synthetic pass. The commands below compile the x86-64 capture and the Color host and run them without EU4. A pass is not acquisition and not B2 acceptance.
2. Acquisition. One process, only after `--authorize-live` and `EU4_B2_COLOR_AUTHORIZE=yes`, and only when `gog_identity()` reports the pinned SHA-256. Before that process starts, the launcher builds `benchmark/.build/b2_color_driver` and runs its `match-vertex` path on `ea46e32:results/20261031T144434Z-metal-draw/matched.glsl`. A missing blob, a compile failure, or a mismatch does not start EU4. The operator stays on the main menu, opens Options, and closes it. If that produces no fixture, they start one single-player game, open the Esc menu once the map is up, and close it. They acknowledge the step actually reached by writing `menu-options` or `ingame-esc` to the launcher's stdin. `menu-options` certifies only a fixture first seen during that menu step. Once it is sent with no fixture, a later fixture needs `ingame-esc`. A viewport above 2048 is an acquisition failure; do not raise the cap. A Color program whose draws all fail the fixed-state checks is an acquisition failure. Record the counters and stop. Do not widen the checks and do not launch again in this implementation.

   `acquired` means the fixture and the acknowledgement for its phase both exist. `incomplete` means a fixture was written but the matching acknowledgement did not arrive; the fixture stays on disk. `acquisition-failed` means no fixture. The recorded phase is the operator's report of the procedure step, not a reading of EU4's UI. A fixture that lands in the moment after `menu-options`, before the Esc menu is open, is recorded as in-game. B2 accepts one authentic Color draw; it does not identify the widget that submitted it. The session metadata records the historical object, `historical_vertex_match`, and the matcher path `benchmark/.build/b2_color_driver`.
3. B2 acceptance. The frozen fixture replays in GL, then Metal. `docs/status.md` changes only when coverage mismatches are 0 and the channel cap holds. `color-note.md` records provenance, the acknowledged UI step, draw parameters, hashes, and the compare.

## Preflight

From the repository root, on the target Mac:

```text
python3 -m unittest tests.test_b2_color tests.test_metal_color_ref
```

`tests.test_metal_color_ref` keeps the original four-argument host path. `tests.test_b2_color` is the x86-64 capture, identity, and indexed replay check. Neither command launches EU4.
