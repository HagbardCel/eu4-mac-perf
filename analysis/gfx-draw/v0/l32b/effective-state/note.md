# L3.2b-I — Effective state of the authentic Simple draw

## Provenance

- Campaign: `eu4-l32b-effective-state`, OpenCode `opencode/big-pickle`, one checker round, `pass`
- Candidate commit: `27d9e98e368016eaf840a522728b6c74fee3cf86`, parented on the L3.2a merge `1b5a82ab734b850ad444a1dc845a74d04c72c226`
- Capture session: `9a8a50c4-c42c-40da-b3db-9472820ce477`; PID `44927`
- Observer library SHA-256: `0f53b00e4036208225671b85527c4e4adea4530db8d2971f1a67e42f38bdc69e`
- Original executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- One authorized launch of three; launch receipt and capture plan are in the campaign run root (`.local`).
- Capture: `.local/automation/eu4-l32b-effective-state/captures/l32b-i-effective-state`
- Packet: [packet/](packet/) and [packet-manifest.json](packet-manifest.json)
- Independent reviewer: `eu4-l32b-review-eu4-l32b-effective-state-r2`,
  `opencode/iceman-free`, outcome `qualified`; report [review.json](review.json).
  It reviewed the corrected checker/candidate commit `8f154e4…` and supersedes
  the earlier `r1` review, which ran under the pre-remediation checker.

## Established inputs

The selected `_GfxDraw` is `call_id` 11 at caller `0xedfcd1`, mode 5, effective count 4,
non-null shader. The accepted inputs are from the same session:

| Input | Evidence |
| --- | --- |
| `vertex_buffer_bindings` | draw master slot 0 header matches the create record for `vb_3` (`call_id` 5); stride 24, offset 0, count 4 |
| `vertex_buffer_contents` | `vb_3` create payload, 96 bytes, SHA-256 `f13dea22399b9649d84a09965ec6b21bc13b72db3c17049916a675922a39c8ab`; no later update superseded it |
| `matrix_constant` | `_GfxSetConstantBuffer` `cb_1` (`call_id` 8), slot 0, selector -1, header `0x7fce16f0beb0`, 4 vec4 = 64 bytes, SHA-256 `e00f5824739f2976ef02dbc5019f54cf89260b8f882a96b8989eab888b7d5671`; draw meta `constant_slot0_match=true` and `constant_slot0_call_id=8` |

The packet is `incomplete`, `packet_ready=false`. It deliberately still reports missing:
`shader_identity`, `shader_stage_sources`, `vertex_attribute_layout`,
`texture_slot_0_identity`, `texture_slot_0_contents`, `sampler_state`,
`render_state_blend`, `render_state_depth_stencil`, `render_state_rasterizer`,
`scissor_state`.

Packet sidecars: draw master snapshot 369 bytes SHA-256
`0d0337f4a4c448222c8453a224a2b76546ed73810904a606893fb2c455592373`;
`vb_3` 96 bytes; matrix 64 bytes. All are copied into the packet and listed in the
manifest.

## Implementation

The agent independently selected the observation rather than receiving a hook list:

- `_GfxSetConstantBuffer` is observed at its safe 9-byte entry prefix
  `55 48 89 e5 48 8b 07 89 c9`; slot-0 current bytes are retained and cross-checked
  against the draw-time constant-slot pointer/array.
- Existing L3.1 vertex create/update/bind observations are retained in the capture.
  The draw master's slot-0 header resolves to a capture-local `vb_N`; the packet
  replays the lifecycle to the draw and copies the full current buffer.
- Optional payloads use `try_payload`; a gap becomes `payload_gaps` rather than
  invalidating the structural draw observation.
- The packet builder resolves bindings/contents/matrix and writes hash-verified
  sidecars. No shader, texture, render-state or scissor input is invented.

## Corrected trusted checker

The initial checker accepted a packet if its own matrix/vertex bytes and hashes
were internally consistent. Post-campaign remediation binds the packet to the
original capture:

- matrix bytes must equal the preceding `_GfxSetConstantBuffer` record selected
  by the draw’s constant-slot pointer and draw-time call id;
- vertex contents must use `resource_lifecycle`; the draw master’s slot header
  resolves to the preceding create record, and the binding’s resource id/call id
  must match it;
- resource-observation and draw-snapshot evidence bytes must equal the original
  operation payload/master bytes;
- forged-but-rehashed matrix/vertex/binding/evidence cases now fail.

The existing capture was re-validated in the original pinned agent workspace
(HEAD `1b5a82a`, candidate changes in the worktree) without relaunching EU4. See
[requalification.json](requalification.json); verdict `pass`, packet SHA-256
unchanged at `7f227ad4…`. The original first-round campaign acceptance remains
historical.

## Campaign evidence

- Agent-selected investigation: yes (constant cross-check and lifecycle identity).
- Autonomous checker remediation: none; the first checker round passed. No defect
  was introduced to demonstrate remediation.
- Captures: 1 of 3.
- Independent review: qualified (`r2`, corrected checker/candidate commit;
  supersedes `r1`).
- Human interventions: reviewer model fallback from `opencode/big-pickle` after the
  provider free limit, and the final PR/merge decision.
- Restartability after a completed capture was not exercised in this run; the
  campaign state and launch counter preserve the completed capture for resume.

The verification manifest changed through the post-campaign snapshot-helper
path fix and the corrected checker; the final digest is `f3c87a4a…`. The
corrected checker re-ran against the original workspace and passed; candidate
code and capture are unchanged, and the packet bytes are unchanged.

## Scope

This establishes selected vertex contents, binding identity and the matrix for
replay. It does not establish shader/texture/sampler/state closure, Metal
execution, pixels, or `packet_ready`. Those belong to L3.2b-II.
