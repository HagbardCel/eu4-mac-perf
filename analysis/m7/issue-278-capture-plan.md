# #278 capture-only plan — live default-drawable format identity

Date: 21 November 2026 (Europe/Berlin). Issue: #278 (M7 scientific
continuation). Status: **plan only; no EU4 launch is authorized by this
document.**

## Implementation status (21 November 2026)

- The synthetic observer is implemented as `tests/gfx_drawable_observe_host.m`
  with the regression `tests/test_m7_drawable_observe.py`. It created a legacy
  CGL context on this host and recorded: colour/alpha/depth/stencil 32/8/32/8,
  zero sample buffers/samples, accelerated, double buffered, legacy profile,
  colour 8/8/8/8 unsigned normalized, depth 32, stencil 8, default-window path.
- The live capture records the same fields in the sealed manifest under
  `gl.cgl` and `gl.default_drawable`, without substituting the requested CGL
  attributes for the effective values. The stage-3 pilot seal
  (`47590918-…`, frame 1) records the live identity: CGL 32/8/32/8, no MSAA,
  accelerated, double buffered, legacy, colour 8/8/8/8 unsigned normalized,
  depth 32, stencil 8, `default-window`, draw/read buffer GL_BACK.
- A reconstructed control can now assert the same attachment identity; the
  control itself was not re-run and is not required for the direct
  EU4-versus-Metal comparison. A reviewed target assertion and authority
  re-pin remain outstanding before any causal claim relies on it.

The live observations are in
`.local/dsh/m7-event20-stage3/workspace/.local/m7/event20/live/frame/frame-trace.json`
and summarized in `analysis/m7/m7-exact-prefix-report.md`.

The 90825686 seal proves captured-state completeness (samples, bit depths,
extent, pre-draw colour/depth seeds) but not that an offscreen RGBA8 FBO is the
live window drawable. The corrected GL-control gate therefore reports
`drawable_equivalence=unproven` and stops before executing the renderer. This
plan records the missing observable; it does not relax the four-channel
criterion, which remains **zero differences**.

## 1. Observation to capture (one new seal only)

For the selected draw's default framebuffer, record:

| Field | Required content | Unavailable handling |
| --- | --- | --- |
| CGL pixel-format attributes | effective `kCGLPFAColorSize`, `kCGLPFAAlphaSize`, `kCGLPFADepthSize`, `kCGLPFAStencilSize`, `kCGLPFASampleBuffers`, `kCGLPFASamples`, `kCGLPFAAccelerated`, `kCGLPFAOpenGLProfile`, `kCGLPFADoubleBuffer`, `kCGLPFAStereo` | Record `unavailable:<reason>`; never infer a default |
| Default-drawable colour format | `GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE`, `GL_FRAMEBUFFER_ATTACHMENT_RED/GREEN/BLUE/ALPHA_SIZE` on the default framebuffer, plus any queryable internal format | Record `unavailable:<reason>` |
| Default-drawable depth format | component type and depth size on the default framebuffer | Record `unavailable:<reason>` |
| Default-drawable stencil format | component type and stencil size, or `absent` | `absent` is a fact, not a failure |
| Window / back-buffer identity | drawable id or window surface id, draw/read buffer enums, double-buffer state, extent, origin | Record `unavailable:<reason>` |
| Render-target path | `default-window` vs an FBO/renderbuffer path, including the bound framebuffer name at the selected draw | Refuse the capture if the path cannot be established |
| Renderer / profile | `GL_VENDOR`, `GL_RENDERER`, `GL_VERSION`, legacy vs core profile, sRGB capability | Record the strings verbatim |
| Sample configuration | `GL_SAMPLES`, `GL_SAMPLE_BUFFERS`, sample positions if exposed | Record `unavailable:<reason>` |

The record must be attached to the same seal as the selected draw, under the
drawable's own observation, with the capture's existing identity and blob
verification. A value that the driver cannot expose is recorded as explicitly
unavailable; it is not defaulted from the requested CGL attributes.

## 2. Synthetic verification before any launch

1. Build a small native legacy-CGL host that creates a window or offscreen
   context, queries every field above, destroys the context, and prints the
   record.
2. Run it once on this host and confirm the record is complete and internally
   consistent (bit depths vs component types, sample count vs sample buffers,
   draw/read buffer enums).
3. Add a regression test that compiles/runs that host and validates the record
   shape, including the `unavailable` representation.
4. Preserve and restore every GL state the query touches; the observer must not
   change the captured draw's state.

No EU4 launch is permitted until these synthetic queries pass.

## 3. One newly justified sealed capture

- Use the existing capture harness; do not add a one-off launcher.
- Budget: **at most one** newly sealed EU4 capture for this observation,
  justified only if the synthetic queries pass and the harness records every
  field above. A failed configuration attempt does not consume the scientific
  observation, but each attempt must fail closed without a seal.
- The new seal gets a new session id, present index and seal digest. It must
  not reuse, relabel or combine the 904 or 90825686 seals.
- Restore `settings.txt` byte-for-byte and terminate the process as the existing
  harness does.

## 4. Comparison contract

- Run the reviewed original-OpenGL control against the **new** seal's own
  restricted inputs, with the new drawable-format identity in the package.
- The control target must report the same identity (component types/internal
  formats, samples, render-target path). This also requires the native executor
  to query and report its own target identity, and the authority digests to be
  re-pinned after that reviewed change. An identity mismatch is
  `unsupported_drawable`, not a silent fallback.
- Authenticate the control independently: seal/stream/checkpoint, signed
  receipt bound to the registered renderer, recomputed exact four-channel BGRA
  comparison. A signature alone is not proof of original-API execution.
- `metal_blame_allowed` becomes true only for that verified scope and only if
  the comparison is byte-identical. Any residual keeps it false.

## 5. Fallback if an equivalent offscreen target cannot be established

Investigate a controlled original-drawable or in-process diagnostic (same
process, same window drawable, one draw) before changing replay heuristics.
Do not run further speculative replay experiments against 904 or 90825686.

## 6. Non-claims

This plan does not claim renderer parity, exact GL equivalence, a Metal
attribution, or that the missing format identity explains the 347-pixel
residual. It records the smallest next observation and the conditions under
which it may be made.
