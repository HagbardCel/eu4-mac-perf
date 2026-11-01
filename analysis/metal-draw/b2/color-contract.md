# Color draw contract

Color is the selected B2 candidate. This note supersedes the zero-uniform mesh layout as the active candidate. It does not replace the evidence in #126 or #127. #126 remains an unmerged, incomplete mesh experiment. #127 records a bounded static feasibility result for mesh `0x14c8404`. Neither is a prerequisite for Color. This note does not authorize a capture, a hook, or a fixture schema.

## Supported layout

The historical submission was one indexed `glDrawElements` of triangles, 300 vertices, one shared array buffer, stride 12. Position is `GL_FLOAT` size 2 at offset 0. Color is normalized `GL_UNSIGNED_BYTE` size 4 at offset 8. Those are formats, offsets, and stride. Original GL attribute locations were queried with `glGetAttribLocation` and were not retained. The Stage D Metal shaders assign `[[attribute(0)]]` and `[[attribute(1)]]` during normalization. A later capture reads the two queried locations and maps them onto that fixed interface. It does not require the live program to use locations 0 and 1.

The linked-program requirement is one active uniform, `UNIFORM0`, an array of four `vec4` values: one entry of size 4, not four active-uniform entries. It holds all 16 matrix floats in column order. No other active uniform, and no active sampler. This is not a measured `GL_ACTIVE_UNIFORMS` value from the historical draw. Unused sampler helpers in the shared GLSL header are not active uniforms and must not reject the program.

Depth off, blend off, back-face culling, and a clockwise front face were recorded for that draw. A later capture still has to read them.

`float2` position plus `v_TEXCOORD1` only filters candidates. Other `in_PDX_COLOR` programs use `float4 vPosition`. An accepted program must match Color vertex and fragment semantics, the two-attribute interface above, and the `UNIFORM0[4]` interface. Two source substrings are not that proof.

## What a later capture must verify

Unsupported cases are rejects. This note does not implement the checks.

- One bound index buffer, a supported index type, and an offset, count, and range that stay inside that buffer.
- All 16 matrix floats, column order preserved.
- Both shader stages and the effective attribute and uniform interface.
- A fixed render-target format, size, and initial contents.
- Viewport and scissor either captured or rejected when unsupported.
- Stencil, MSAA, sRGB, and color masks either matched or rejected.
- Reference pixels from an independent GL replay of the same inputs and initial state.

## Evidence

Reconstructed Color source, the compiled MSL pair, and synthetic GL–Metal pixel equivalence in `tests/test_metal_color_ref.py` are established. That test draws with `glDrawArrays`. The historical EU4 submission was indexed, so the test does not cover the indexed path.

Commit `ea46e32` retains `results/20261031T144434Z-metal-draw/result.json`: 246 non-clear pixels, 300 vertices, stride 12, byte-normalized color, and the depth, blend, and culling flags. The complete original-input fixture and the independent GL reference pixels were not retained. That readback is a standalone Metal submission. It is not pixel equivalence, and it does not satisfy B2.

The Color contract is a candidate for one authentic indexed EU4 draw. The observed vertex formats, offsets, stride, matrix semantics and render state define the supported layout. Original GL attribute locations are captured and mapped to the fixed Metal shader interface. Eligibility requires the linked program to contain only the expected four-element `UNIFORM0` array, with no other active uniforms or samplers, and to match the Color vertex and fragment semantics. Index data and all pixel-affecting state must be complete and supported. The historical Color draw has no retained original-input fixture or independent GL reference; synthetic conformance and the historical Metal readback do not satisfy B2 acceptance.

## Limits

EU4 resolves `glDrawElements` through `dlsym`, including the ARB alias. Ordinary Mach-O interposition cannot be assumed to see this draw. This note does not add a hook.

Later idle sessions did not see this program. Color is a UI path, not the paused-map frame. One prior session does not make it available. A future acquisition has to name a UI context that submits it. That session is not authorized here.

This is the last preparatory note. The next milestone, separately authorized, is one complete original Color fixture compared with Metal.
