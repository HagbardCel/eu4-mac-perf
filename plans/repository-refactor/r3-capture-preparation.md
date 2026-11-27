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

## Live attempt on the credential-free path

Implementation revision `06da3df4` launched the pinned x86-64 original on
macOS 27.0.1 build `26A434`. BetterDisplay first timed out during setup before
EU4 spawned. After the background display was restored, one EU4 process
reached present 72 with 283 operations. Session
`530e3df2-f0f9-4fd4-8581-e03f680e869b`, manifest
`f09ed78a54be8f31c29cbd2e45151f5ab30124f891cd2eb12dae5797a5c0c125`.
The frame records event 273 on `tex:447`. The selected event has no
submission record and its JSONL row is malformed. The manifest and seal
are both incomplete with the trace reason
`selected_submission_or_target_unverified`. This reason covers missing
submission and failed target continuity checks. The retained data does
not identify which subcheck failed.

Artifacts remain in `.local/r3-authentic-capture-live/`. The earlier failed
display setup remains in `.local/r3-authentic-capture/`. The launcher
terminated the owned EU4 process after the incomplete seal was observed,
restored game settings and released the host lease. No active campaign
state or game process remained. The incomplete capture was not published or
frozen. Authentic positive and omission qualification require a complete
original observation before candidate execution.

### Offline submission investigation

The retained original files were inspected read-only. Draw 273's row contains
the selected caller, pinned shaders and draw arguments `(5, 0, 4)`. The row
contains an extra comma where the unsuccessful submission writer emitted no
member. The pre-image and post-observation blobs each have 1,928,448 bytes and
match their recorded hashes. After BGRA-to-RGBA conversion, all 482,112 pixels
differ. This establishes a nontrivial change in the retained image bytes. The
incomplete stream cannot attribute that change to a verified GL submission.
The process streams are empty and there is no interposer error record. The
installed startup dylib still matches the hash in the capture request.

Offline disassembly of the pinned x86-64 executable showed the following:

| Site | Result |
| --- | --- |
| RVA `0x15ea79d` | Save the signed entry `edx` as `first`. |
| RVA `0x15ea7a0`–`0x15ea7aa` | Use entry `esi` when positive. Otherwise take count from `master+0x64`. |
| RVA `0x15ea928`–`0x15ea930` | Load GL mode from `master+0x08` into `edi`, restore first into `esi` and count into `edx`. |
| RVA `0x15ea944` | Tail-jump to the OpenGL `glDrawArrays` symbol stub at RVA `0x18c2600`. |
| RVA `0x18c2600` | Jump through lazy slot VA `0x101c4e840`. The on-disk value points to the stub helper at VA `0x1018c3562`, which pushes the bind offset and enters the common `dyld_stub_binder` helper. |
| Loaded interposer | The retained startup dylib's interpose section names the same OpenGL `_glDrawArrays` import. Its wrapper forwards through the dylib's own GL import. |

The recorder's entry formula matches the engine's argument preparation.
Mode is loaded by the engine at submission time, so the entry value remains
an expectation checked against the observed value. The import is a Darwin
lazy binding for this callsite. The separately injected native x86-64
fixture exercises the same binding and tail jump. Its observer count and
sample query establish one full draw in that fixture. This rules out a
general inability of the Darwin interposer to intercept that dispatch form
and rules out duplicate forwarding in the fixture. The retained original
does not record the resolved runtime slot, observer count or first failing
check. These offline results therefore cannot identify which check failed
in the live process.

The recorder now writes a valid failure member with the first failed check,
observed submission count, expected and observed arguments, context matches,
and available entry/submission/exit target state. Unqueried fields, including GL
errors, are recorded as null. Successful target continuity compares semantic
bindings and attachment properties independently of diagnostic metadata. The
existing `glGetError` check consumes an error when performed. The selected-target
diagnostics avoid additional error consumption after
a disqualifying observation. The fixture checks GL bindings, program, active
texture, buffer and pack state and viewport across the successful observation
and readbacks. Seeded negative controls distinguish argument mismatch,
missing submission, GL error and changed read framebuffer.

### Targeted experiment if runtime confirmation is needed

Use one diagnostic session that stops at the selected original tail jump
and its return to the recorder. Inspect the runtime lazy slot and registers
`edi`, `esi` and `edx`, then check whether the injected wrapper executes
and read the recorder's first-failure fields at exit. Resolve addresses
using the loaded image slide. One wrapper hit with matching arguments
shifts the diagnosis to the recorded context, target or GL error. A
resolved slot that bypasses the wrapper and a zero observation count
identify a runtime binding problem. A wrapper hit with different arguments
identifies an expectation or argument-change problem. End after that one
draw. The diagnostic session does not authorize publishing a replacement
seal.
