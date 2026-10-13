# Current project status

Authoritative summary of what is established, qualified, and blocked. Operational commands live in [profiler and benchmark operations](reference/profiler-and-benchmark-operations.md).


The paused-frame investigation supersedes border batching as the immediate next
action. The profiler now reports semantic attribution and envelope residuals,
uses acknowledged measurement epochs, and segments GPU timestamps by context
and pass. Sample windows now require complete plain neighbors, state/query
preparation runs unmeasured, GL accounting is aggregated per frame, and writer
output is batched. Draw API coverage fails closed for unknown submission paths.
**Tier-1 qualification ended with WP11** (`33339073`, failed v4 admission). Absolute
timings are **not** quantitatively qualified for production claims. The **intrusive diagnostic contract** is available via preflight; **Phase C** live
capture uses `run --diagnostic-only` (R–C–R–C–R after warm-up):

```sh
python3 benchmark/eu4_frame_model.py preflight --intrusive-diagnostic-contract
python3 benchmark/eu4_frame_model.py run --diagnostic-only --output results
```

See [live diagnostic measurement contract](live-diagnostic-measurement-contract.md),
[post-WP11 roadmap](eu4-live-diagnostic-roadmap.md), and
[the verification record](../analysis/frame-model-verification.md).
Default `run` (without `--diagnostic-only`) still requires passing offline causal admission.

The [Phase II draw-path decision](../analysis/draw-path-decision.md) remains
historical screening evidence: 31 complete paused frames at 5,863 draws/frame
identified a recurring border run. Its trace failed its intrusion gate, so it
does not establish an optimization benefit. Post–PR #40, consolidated rendering
findings and the Gfx→Metal strategy live in
[macOS rendering findings](eu4_macos_rendering_findings.md) and
the canonical [rendering roadmap](eu4_recommended_strategy.md), with its
[cost-based ranking of alternatives](eu4_rendering_alternatives_ranked.md).
R0-M measurement tooling is documented in [docs/r0-measurement.md](r0-measurement.md).
The local trace can be re-screened:

```sh
python3 benchmark/eu4_draw_trace.py screen-partial results/20260928T113359Z-draw-trace
```

The pinned offline inventory and synthetic GL harness can be verified with
`python3 benchmark/eu4_draw_static.py verify` and
`python3 benchmark/eu4_draw_trace.py preflight`.

## Phase C intrusive diagnostic (qualification)

- **R-mode** segments in the sealed Phase C capsule provide credible baseline structural evidence for the intrusive contract.
- **C-mode** absolute timings are **not** quantitatively qualified for production claims (same tier as post-WP11 profiler admission).
- Canonical capsule: [../results/20261003T210202Z-intrusive-diagnostic/report.md](../results/20261003T210202Z-intrusive-diagnostic/report.md).

## R0-M integrated session (`20261012T210806Z`)

Partial qualification only ([../results/INDEX.md](../results/INDEX.md), [../results/20261012T210806Z-r0-measurement/validation.md](../results/20261012T210806Z-r0-measurement/validation.md)):

- CPU reconciliation obtained in epoch A.
- Drawable census **not evaluated** (`unsupported_drawable` readback policy).
- Automated stack attribution failed on an unsupported `sample` formatting variant.
- Acquisition status **`incomplete_gates`**; frame staticity and lossless-skip candidates were **not** evaluated.

## Pinned environment (summary)

This repository measures the **GOG** copy of Europa Universalis IV at
`/Applications/EuropaUniversalisIV`. The collector refuses to record a game run
unless the running `eu4` process has that exact executable path. It reads the
GOG launcher's separate user-data folder, currently
`~/Documents/Paradox Interactive/Europa Universalis IV GOG`.

The installed build inspected on 26 September 2026 is EU4 v1.37.5.0 Inca,
x86-64, linked to OpenGL/GLUT and GOG Galaxy. The default fixture is the GOG
folder's disposable `save games/Venice1444_11_11.eu4`. Its presence and hash
are checked at run time. The save metadata identifies Venice on 1444.11.11,
and GOG `settings.txt` currently reports `autosave="NEVER"`.
The active GOG playset currently includes Proper 2K UI Enhanced; keep the same
playset and DLC selection throughout a comparison.

