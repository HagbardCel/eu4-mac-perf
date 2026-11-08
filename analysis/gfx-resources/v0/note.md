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

- 250 operations matched: 247 known creates, the `vb_126` full update, and
  the two viewport observations.
- 5 operations skipped: null-source creates whose initial contents remain
  unknown. They are replayed as allocations and retain identity, but are not
  content-qualified.
- 1 operation unsupported: the bind predates the `words_null` flag. Its
  explicit-versus-null semantics cannot be established from this artifact, so
  it is not compared. Do not retroactively invent pointer-nullness; a recapture
  with the corrected observer can qualify it.
- `vb_126` demonstrates the intended transition: unknown initial allocation
  followed by a full known update becomes comparable after the update. A
  partial update on an unknown resource remains unqualified.
- The aggregate is `unsupported` / `partial_evidence`, not a full-sequence
  `pass`; the 250 matched observations remain available as known-resource
  evidence.
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
