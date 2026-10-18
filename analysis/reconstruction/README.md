# Renderer reconstruction (RE0–RE3) artifacts

Per-function reconstruction contracts and evidence inventories, per the
[RE0–RE3 contract](../../docs/renderer-reconstruction-and-differential-validation.md) and the
[Oracle plan of record](../../docs/oracle/PLAN_OF_RECORD.md). Static analysis only — nothing
here launches EU IV.

| Function | Entry RVA | Status |
| --- | --- | --- |
| `_GfxSetConstantBuffer` | `0x15ec36a` | RE1 reconstruction complete ([reconstruction](_GfxSetConstantBuffer/reconstruction/README.md)); RE0 contract ([contract.md](_GfxSetConstantBuffer/contract.md)) and offline execution pilot ([pilot.md](_GfxSetConstantBuffer/pilot/pilot.md), [receipt](_GfxSetConstantBuffer/pilot/observations-2026-10-18.json), [frozen scenarios](_GfxSetConstantBuffer/pilot/scenarios.json)) complete |

Raw binaries, decompiled pseudocode and the searchable corpus stay under `.local/` (see
[conventions](../../docs/conventions.md)); the files here are the tracked analysis.
