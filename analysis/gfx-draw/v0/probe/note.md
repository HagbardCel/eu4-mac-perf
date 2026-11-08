# L3.2a — authentic Simple draw probe (v0)

## Provenance

- Pinned executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- Operator-authorized original EU4 launch, 2026-11-08 (UTC)
- Command:

  ```
  python3 -m automation.live_gfx capture \
    --target _GfxDraw --target _GfxSetPrimitiveType \
    --until-target-draw --caller-rva 0xedfcd1 \
    --timeout 120 \
    --output .local/gfx-captures/simple-draw-probe \
    --authorize-live
  ```

- Session: `d5af7baf-cc1e-4def-a5f4-96e1d5325641`; PID: `34926`; image base `0x100000000`
- Observer library SHA-256 at capture time: `dc5ca8c44bc7d00ade729c60b2b17140c06079914d023b52d9a35dba794676a9`.
  This is the build that produced the capture. The subsequent L3.2a remediation
  (unique-target selection and positive-count validation) builds as
  `5bac2f9dbd2568f45272b74730502014adfdb971f49a5856c21e42d75b5b3811`; it does
  not change this capture's single zero-count qualifying draw or its evidence.
- Startup context: `complete`, one settings pair, one qualifying init; operations `state=sealed`,
  `integrity_ok=true`, `records_copied=true`, `observed=2`, `events=1`, no overlaps,
  no refused/unmatched/unavailable/overflow/in-flight records.
- Owned process termination: graceful `terminate`, not killed; `game.stdout`/`game.stderr` empty.
  No crash report was produced.
- Original artifact: `.local/gfx-captures/simple-draw-probe` (not tracked)
- Promoted capture: [capture/](capture/)
- Packet: [draw-packet.json](draw-packet.json) plus [payloads/4-draw.bin](payloads/4-draw.bin)
- Packet command (run against the promoted capture):

  ```
  python3 -m automation.live_gfx packet \
    --capture analysis/gfx-draw/v0/probe/capture \
    --output .local/gfx-packets/simple-draw-probe
  ```
- No Metal backend was injected into the captured process.

## What the capture establishes

The first retained `_GfxDraw` is the Decision 0003 target:

| Field | Value |
| --- | --- |
| caller return RVA | `0xedfcd1` |
| invocation | `call_id=4`, `completion_seq=1`, thread `10792846` |
| mode at `master+8` | `5` (`GL_TRIANGLE_STRIP`) |
| incoming count (`esi`) | `0` |
| first (`edx`) | `0` |
| effective count (`master+0x64`) | `4` |
| shader at `master+0x70` | non-null |
| preceding `_GfxSetPrimitiveType` | engine enum `4`, `call_id=3` |
| engine enum → GL mode | `4 → 5`, matching the tracked primitive table |
| viewport words | `[1117, 1728, 1065353216, 0, 0, 0]` |
| decoded GL viewport | `x=0, y=0, width=1728, height=1117` |
| render target | default (null at `master+0x78`), window `1728×1117` |
| bound vertex slot 0 | session-local header `0x60000125a750`, offset `0`, stride `24`, count `4` |

This is the decisive experimental result: **the passive original path submits
`_GfxDraw(ctx, 0, 0)` with an effective count of four**, not zero. The earlier
observed zero is consistent with the Metal draw shim having failed to populate
the vertex-buffer header (the shim path is not the passive original path).

Only the target draw was retained. The preceding `_GfxSetPrimitiveType`
observation was folded into the draw metadata and aggregate
(`primitive_sets=1`), so unrelated observations did not consume record slots.
The ten-fold capture budget (`retention=state`) was not approached.

## Packet status

`draw-packet.json` is `incomplete` and `packet_ready=false`, which is expected
for L3.2a. Established inputs:

- `draw_invocation`
- `primitive_semantics`
- `viewport`
- `render_target` (default-backbuffer identity/dimensions; BGRA8Unorm is a
  project-derived P5.1 assumption, and pre-draw contents are not captured)
- `vertex_buffer_bindings` (structural; identities are session-local)

Missing inputs reported explicitly:

`shader_identity`, `shader_stage_sources`, `vertex_buffer_contents`,
`vertex_attribute_layout`, `texture_slot_0_identity`,
`texture_slot_0_contents`, `sampler_state`, `matrix_constant`,
`render_state_blend`, `render_state_depth_stencil`,
`render_state_rasterizer`, `scissor_state`.

No shader source, vertex content, texture pixel, constant or render state was
invented or synthesized.

## File hashes (packet-referenced)

| File | SHA-256 |
| --- | --- |
| `startup.status` | `f95238c439e9dd2fa560b80a8f3adc6fb296decef3a8e3115e6e0e955ffb82c0` |
| `metadata.json` | `d14caa6bd5b2fa9f21f21c472d7648fbc1503e96864511488a7a65fcfc929861` |
| `summary.json` | `3a09ce8073b7010a318d87e8a3b5eaebcbba6a819f00638a7e9b3238bad0c82a` |
| `events.jsonl` | `19dd7f1ab66c33ead8e674ac16758ad8f31faf727f73215bf86b6984f838cf46` |
| `operations-summary.json` | `74372b24b65efbac922431b5f36ec274a4b6e07f929fbe224fcb7f2d5b6f0e15` |
| `operations.jsonl` | `76aaa86fe7e0566c746c301da5e676791718c80edf73fd784c096a40b3ca783a` |
| draw master snapshot | `adeb931f2c57dfe4fb88d46084a1b19eae46424c80033ef6f16c52a6bbf0a227` |

The packet copies the snapshot to `payloads/4-draw.bin` with the same SHA-256.

## Scope

This artifact establishes the authentic draw invocation and its effective
parameters. It does not establish shader/texture/constant/render-state inputs,
GPU state, pixels, or Metal equivalence. No Metal workload ran. Pointer values
in the packet are session-local identities, not cross-run or Metal pointers.

## Next step

L3.2b starts from this invocation. The revised design keeps effective resource
state in bounded tables (no historical event log), captures the selected
shader object's two stage sources, initializes/binds the texture and sampler,
reads the shader-object attribute layout and constant slots at the draw, and
retains the whole selected vertex buffer. Texture-mutation closure must be
path-specific, and the interior `_GfxUpdateTexture` site must not be registered
without a separate call-through qualification. A packet is `ready` only when
every required byte is present in a self-contained local packet.
