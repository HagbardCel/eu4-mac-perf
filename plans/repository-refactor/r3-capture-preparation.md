# R3 authentic capture preparation

The selected workload is the color-only offscreen draw reached from
`_GfxDraw` caller `0xed7ba9` in the retained M7 event 273 seal. The
selection document pins the caller, both shader digests and the attachment
format and extent. Event and object numbers are local to each seal.
[Protected qualification policy](../../automation/qualification-policy-v1.json)

## Retained evidence

| Workload | Assessment |
| --- | --- |
| Window draw 18 from seal `90825686…` | The sealed evidence lacks default drawable equivalence and lossless D32 depth. |
| Offscreen draw 273 from `47590918…` | Event 275 has a lossless later texture inventory. The seal lacks a verified texture lifetime through that read and an immediate attachment observation. |
| Offscreen draw 14 from `47590918…` | The later RGBA texture is byte-identical to the verified full white initialization. Every channel byte is 255. |
| Same caller at draw 286 from `2da0a8fc…` | Lifecycle history verifies. The retained target has incomplete direct observation and stencil applicability. |
| `afd444f2…` and `1efc8880…` | The inventory lacks a direct observation of the selected offscreen workload. |

The authorized alternative is event 273 because the window reference is
inapplicable. Qualification requires one new seal with the complete
observation and retains each earlier finding under its own identity.
[Window control report](../../analysis/m7/gl-control-qualification-90825686.md)

## Required observation

Capture a unique call with the pinned shader and resource identity. Require
the same complete single-sample texture attachment at entry and exit,
COLOR_ATTACHMENT0 draw and read bindings, direct lossless pre/post color,
and checked depth and stencil absence. The selected original observation
refuses a GL error. The sealed capture records the pinned executable digest, process
architecture, macOS build, Apple GL implementation and context profile.
Candidate execution uses the recorded process architecture and checks macOS
build and the observed GL identity before qualification.

The archived instruction listing for `_GfxDraw` at RVA `0x15ea789`
shows the dirty-uniform indirect call at `0x15ea806` and vertex attribute
setup at `0x15ea91a`. Those operations can change the effective uniforms
and geometry relative to entry. The last path loads mode, first and count
and tail-jumps to `glDrawArrays` at `0x15ea944`. There are no engine
instructions between the GL return and the draw exit observer on this path.
The exit state therefore describes the effective linked resources, uniform
values, geometry and raster state after a successful submission. The
missing-shader branch can return without submitting a draw.

The recorder measures the actual `glDrawArrays` arguments and verifies one
submission for the selected engine call. It checks the same framebuffer and
level-0 attachment at entry, submission and exit, along with the matching
read binding, single-sample draw buffer and depth/stencil absence. The
pre-state image comes from entry and the post-state from exit. The sealed
submission record links those boundaries. The candidate observer queries
shader source digests, effective GL state and attachment bytes. The verifier
requires exact equality and a correctly initialized omission with a different
attachment. Readback preserves bindings and pixel-pack state. [Original draw dossier](../../analysis/reconstruction/gfx-semantic/v1/draw/_GfxDraw.md)

For live development, use the reviewed scene selection and a fresh output
directory on a host with a free EU4 lease. A capture-health failure or
incomplete seal ends the attempt. Verify workload identity before publishing.
