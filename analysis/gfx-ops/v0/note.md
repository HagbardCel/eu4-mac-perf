# Live graphics operation capture (v0)

## Provenance

- Pinned executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- Captured: 2026-11-08 (UTC), one operator-authorized original EU4 process
- Command: `python3 -m automation.live_gfx capture --target _GfxSetViewPort --max-events 16 --duration 5 --timeout 120 --output .local/gfx-captures/viewport-01 --authorize-live`
- Original artifact: `.local/gfx-captures/viewport-01` (not tracked)
- Promoted copy: `analysis/gfx-ops/v0/capture/`
- Session: `f975f721-ec09-4a57-87b3-4e7a53fb3243`
- PID: `43379`; image base: `0x100000000`
- v0 startup summary: complete, sealed, one settings pair, one qualifying init
- Operation summary: `integrity_ok=true`, `records_copied=true`, `events=7`,
  `overlaps=0`, `unmatched=0`, `unavailable=0`, `in_flight=0`
- Coverage: seven `_GfxSetViewPort` observations; one distinct input; no overlap

No Metal backend was injected into the captured process.

## Observed operation

`_GfxSetViewPort` at RVA `0x15ea4f8`. The first observation comes from the
`CGraphics::InitGfx` call at `0x149b21a`; its return address is
`0x149b21f`. The remaining six come from the `CGraphics::Setup2dGraphics` call
site; their return address is `0x149d046`. All seven run on the init thread
(`thread_id=10049942`).

The 24-byte source image and the original master state at `[master+0xac]` are
identical for every call:

```
[1117, 1728, 1065353216, 0, 0, 0]
```

The unusual third word is the original rect image; it is preserved exactly.
The capture records both the input image and the resulting master image.

## Replay

`python3 -m automation.live_gfx replay --capture analysis/gfx-ops/v0/capture --output .local/gfx-diffs/viewport-01` passes with the current Metal shadow worker:

- startup precondition matched: configure echo, init success/initialized,
  width 1728, height 1117, source 1, primitive 4, zero viewport, dirty 0;
- all seven `set_viewport` responses matched the captured six words exactly;
- `shutdown_ok=true`.

A pass establishes CPU-visible viewport-state equivalence for these captured
invocations only. It does not establish GPU viewport state, pixels, or complete
engine-path equivalence.

## File hashes

| File | SHA-256 |
| --- | --- |
| `startup.status` | `39740c522ab004221d5a680f71669fe7200d35230fe81dc692596f29ecebe852` |
| `metadata.json` | `19d1dd7fc023920b51f046b8b858f192503d50f8569e8c8f90e63c6b0c098aea` |
| `summary.json` | `3a09ce8073b7010a318d87e8a3b5eaebcbba6a819f00638a7e9b3238bad0c82a` |
| `events.jsonl` | `119615841984457ac5af3b8193a9c417a6c340d9100f8e44259184f2f8b529d6` |
| `operations-summary.json` | `37cfcbadf44b5d41e82fc8b2d26762e403b638b3070ca8668f03a2916d109a52` |
| `operations.jsonl` | `8cbf4e46e01651778bc840ae72d87c8a5b37e52580a872e2744fc1921ef026d8` |
