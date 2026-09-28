# EU IV GOG power report

Power values are estimated system-wide SoC figures from powermetrics; they are not EU IV-only watts.

## Screening observation

One comparable paused/speed-5 pair at 120 Hz: CPU power +3.61 W, GPU power -0.82 W, combined CPU + GPU power +2.74 W, and EU IV CPU activity +139.6 percentage points. This is a screening signal; test a practical configuration change before deciding which comparisons merit repeats.

At speed 5, Low Power Mode changed median estimated CPU + GPU power from 12.45 to 5.86 W (-52.9%), and measured throughput from 10.72 to 7.16 days/s (-33.3%). Estimated days per CPU + GPU joule changed +41.8%. This is a first-pass comparison; capture durations differ and end dates were entered manually.

Paused 60 Hz configuration versus 120 Hz baseline: GPU power 1.67 → 0.58 W, CPU power 7.99 → 8.32 W, and combined CPU + GPU power 9.70 → 8.92 W. Window mode and game resolution also changed, so this measures the whole configuration rather than refresh rate alone; the combined saving is small enough to warrant caution before treating it as a repeatable gain.

## Formal attribution

Inconclusive: idle, paused, and speed 5 each need three verified 120.0 Hz Normal-mode runs.

The three-repeat rule is a confidence threshold, not a required run count for screening configuration changes.

## Measurements

| Scenario | Hz | Mode | Graphics engine | Trials | CPU W | GPU W | EU IV CPU % | GPU active % | Thermal | Days/s | SoC days/J | FPS |
|---|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| paused | 120.0* | normal | standard | 1 | 7.43 | 1.684 | 119.5 | 56.965 | Nominal | — | — | — |
| paused | 120.0* | normal | standard | 1 | 8.255 | 1.565 | 126.1 | 54.817 | Nominal | — | — | — |
| paused | 120.0 | normal | multithreaded OpenGL | 1 | 11.948 | 2.839 | 175.2 | 77.178 | Nominal | — | — | — |
| paused | 120.0 | normal | standard | 1 | 7.986 | 1.673 | 125.75 | 51.191 | Nominal | — | — | — |
| paused | 60.0 | normal | standard | 1 | 8.321 | 0.585 | 135.15 | 31.652 | Nominal | — | — | — |
| speed1 | 120.0 | normal | standard | 1 | 8.223 | 1.698 | 130.55 | 54.037 | Nominal | 0.48 | 0.0484 | — |
| speed3 | 120.0 | normal | standard | 1 | 8.66 | 1.6 | 148.65 | 48.235 | Nominal | 1.666 | 0.1624 | — |
| speed5 | 120.0 | low | standard | 1 | 5.177 | 0.689 | 248.0 | 20.437 | Moderate, Nominal | 7.157 | 1.2201 | — |
| speed5 | 120.0 | normal | standard | 1 | 11.599 | 0.858 | 265.3 | 26.597 | Nominal | 10.723 | 0.8608 | — |

*Display refresh was not verified while EU IV was in the foreground; the listed rate may be from before the game regained focus or from the operator's requested mode.

Compare repeated runs with the idle control before attributing changes to rendering or simulation.
A paused GPU increase supports a rendering contribution; a speed-5 CPU increase supports a simulation contribution.
SoC days/J divides days/s by estimated CPU + GPU watts; it excludes other system components.
The power graph stacks CPU and GPU components, not total wall or battery power.
