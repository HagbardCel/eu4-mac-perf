# Live vertex-buffer resource capture (v0)

## Provenance

- Pinned executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- Captured: 2026-11-08 (UTC), one operator-authorized original EU4 process
- Command: `python3 -m automation.live_gfx capture --target _GfxCreateVertexBuffer --target _GfxUpdateVertexBuffer --target _GfxSetVertexBuffers --target _GfxSetViewPort --max-events 256 --duration 5 --timeout 120 --output .local/gfx-captures/resources-02 --authorize-live`
- Original artifact: `.local/gfx-captures/resources-02` (not tracked)
- Promoted copy: `analysis/gfx-resources/v0/capture/`
- Session: `138b97bc-d055-48e5-b7ea-71debebce5e0`; PID: `62033`
- 256 completed observations; `integrity_ok=true`, `records_copied=true`, `overlaps=0`, `unmatched=0`, `unavailable=0`, `in_flight=0`
- Targets seen: `_GfxCreateVertexBuffer` (252), `_GfxSetVertexBuffers` (1), `_GfxSetViewPort` (2), `_GfxUpdateVertexBuffer` (1)
- Payload sidecars: 248 files; largest 221,280 bytes

No Metal backend was injected into the captured process.

## What the capture establishes

The sequence is authentic original EU4 execution after the qualifying init. The
first observed operations include several large buffers created with a null
source (contents unknown), followed by known small buffers, a bind, viewport
updates, and one update on an unknown-initialized buffer.

`contents_known=false` is recorded for the null-source creates. Those
observations are structurally valid but cannot establish expected contents, so
the replay declines them. The same applies to the update of a skipped resource.
They are reported as `skipped`, not silently compared.

## Replay result

`python3 -m automation.live_gfx replay --capture analysis/gfx-resources/v0/capture --output <dir> --skip-unknown`

- 250 operations matched: known vertex-buffer contents (byte length + SHA-256),
  the binding `vb_3` at slot 0, and the two viewport observations.
- 6 operations skipped: 5 creates with unknown original contents and 1 update
  on a skipped resource.
- Harness teardown destroyed the worker-owned resources; `shutdown_ok=true`.
- The known-resource subset is CPU-resource/binding equivalence for the
  captured sequence. It is not GPU-resource, destruction, or render equivalence.
- The worker's offline buffer/line caps were raised to 4 MiB / 8 MiB to cover
  the authentic 221 KiB payload; larger resources remain bounded and report
  `unsupported`/`unavailable` rather than being fabricated.

## Scope

This artifact establishes observed create/update/bind evidence and known-content
comparison. It does not observe `_GfxDestroyVertexBuffer` (relocation-unsafe
prefix), so lifetimes are guaranteed only across observed creates; a reused
address gets a new `vb_<n>` identity. No pointer or Metal address is compared.
