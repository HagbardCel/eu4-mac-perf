# Intrusive diagnostic report (Phase C)

This capture is **not** eligible for causal or qualified assessment.

## Primary observer metrics (intrusive / unqualified)

- Throughput (update attempts/s, R vs C): **-33.5%**
- R median update wall: **18.536 ms** (thread CPU 0.362 ms)
- C median update wall: **27.779 ms** (thread CPU 0.597 ms)
- Update-thread CPU Δ (live): **235.12133333333335 µs/update**

Aggregate process CPU Δ per update mixes fixed background CPU with per-frame costs when cadence changes (~33%); do not treat it as a pure observer tax.

## Live observer effect brackets

- **C1 vs R1,R2**: swaps -33.5%, process CPU +0.3%, update-thread CPU Δ 234.9 µs/update
- **C2 vs R2,R3**: swaps -33.6%, process CPU -0.2%, update-thread CPU Δ 235.3 µs/update

## Offline mesh prior

- Aggregate counters incremental prior: **~9.25 µs/frame**
- Component-level bias correction: **unavailable**

## C1/C2 attribution

- **C1 semantic exclusive rank** (identified subset only): HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CPdxMeshObject::AddToBucket, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CPdxMeshObject::AddToBucket → CArray<SFlushData>::Append, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CGraphics::PresentScene → CGLFlushDrawable, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CGraphics::PresentScene
- **C1 coverage**: semantic/Update CPU +13.2%, semantic/Render CPU +1.8%
- **C1 top envelope residual**: HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CEU3GraphicalMap::Render (271.571 ms exclusive CPU)
- **C2 semantic exclusive rank** (identified subset only): HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CPdxMeshObject::AddToBucket, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CPdxMeshObject::AddToBucket → CArray<SFlushData>::Append, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CGraphics::PresentScene → CGLFlushDrawable, HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CGraphics::PresentScene
- **C2 coverage**: semantic/Update CPU +13.2%, semantic/Render CPU +1.8%
- **C2 top envelope residual**: HookedUpdateLoop → UpdateOneFrame → CInGameIdler::Idle → CInGameIdler::Render → CEU3GraphicalMap::Render (271.440 ms exclusive CPU)
- Exclusive rank stable across C1/C2: **True**; GL work-count rank stable: **True**

### Cadence arithmetic (secondary)

- Aggregate process CPU Δ per update: **10284.8 µs/update**
- Offline mesh prior: **~9.246519047619048 µs/frame**

## Forensic tail

- Profiler records dropped: **191966** (queue saturation; detail stream biased)
- Flag **2048** marks incomplete GL shadow; `eligible_trace()` discards detail rows for those frames (separate from queue drops).
- Tail gate: **failed** (Forensic tail produced no D/S/U/B/T detail records)
- Structural capture: **failed**; sample perturbation: **failed**; timing: **highly_intrusive_sample_windows_failed_calibration**
- Draw timed samples: **184**
- Sample-window calibration: **failed**
- Forensic tail is isolated from R/C observer-effect brackets; timings are intrusive and unqualified.

