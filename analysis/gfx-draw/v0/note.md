# Authentic Simple draw capture (v0)

L3.2a answers the decisive question: the passive original `EU4` loading-screen
path does submit the Decision 0003 draw.

- Pinned executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- One operator-authorized original launch; session `d5af7baf-cc1e-4def-a5f4-96e1d5325641`, pid `34926`
- First retained `_GfxDraw`: caller `0xedfcd1`, `call_id` 4, mode 5, incoming `esi` 0,
  first 0, effective count 4 from `master+0x64`, non-null shader
- Preceding `_GfxSetPrimitiveType(4)` at `call_id` 3; `4 → 5` matches the tracked
  primitive table
- Capture sealed with `integrity_ok=true`, `observed=2`, `events=1`, no
  overlap/refusal/unavailable/overflow/in-flight counts

The earlier observed zero at this callsite is consistent with the Metal draw
shim's failed buffer header, not with the passive original path.

Artifacts:

- [Probe note](probe/note.md)
- [Promoted capture](probe/capture/)
- [Draw packet](probe/draw-packet.json) and [snapshot](probe/payloads/4-draw.bin)

The packet is `incomplete` and `packet_ready=false`, as expected for L3.2a. It
establishes the draw invocation, primitive semantics, viewport, default render
target and structural vertex bindings, and explicitly reports the remaining
inputs missing: shader identity/sources, vertex contents/layout, texture and
sampler, matrix constant, render-state components and scissor.

No Metal workload ran. No pixel or GPU equivalence is claimed. L3.2b now starts
from this exact invocation and must capture its effective inputs in one
coherent session.
