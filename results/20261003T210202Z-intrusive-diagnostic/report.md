# Intrusive diagnostic report (Phase C)

This capture is **not** eligible for causal or qualified assessment.

## Live observer effect (intrusive / unqualified)

- **C1 vs R1,R2**: CPU +0.3%, swaps -33.5%, update CPU Δ 234.9 µs/update
- **C2 vs R2,R3**: CPU -0.2%, swaps -33.6%, update CPU Δ 235.3 µs/update
- **Aggregate**: CPU +0.1%, swaps -33.5%, update CPU Δ 235.1 µs/update

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

### Process CPU / update sanity (preferred vs offline prior)

- Aggregate process CPU Δ: **10284.8 µs/update**
- Offline mesh prior: **~9.246519047619048 µs/frame** (order-of-magnitude only)

## Forensic tail

- Tail gate: **failed** (Forensic tail produced no D/S/U/B/T detail records)
- Structural capture: **failed**; sample perturbation: **failed**; timing: **highly_intrusive_sample_windows_failed_calibration**
- Draw timed samples: **184**
- Sample-window calibration: **failed**
- Forensic tail is isolated from R/C observer-effect brackets; timings are intrusive and unqualified.

