# GOG EU IV state-cache validation

One paused, hands-off A–B–A–C–A–D launch; B=texture, C=texture+vertex, D=texture+vertex+uniform.

**No cache stage engaged. These power comparisons do not measure state deduplication.**

| Phase | Swaps/s | EU IV CPU ms/s | CPU W | GPU W | Texture skipped | Vertex skipped | Uniform skipped |
|---|---:|---:|---:|---:|---:|---:|---:|
| a1 | 50.5 | 1117.98 | 7.902 | 1.842 | 0 | 0 | 0 |
| b | 51.37 | 1187.72 | 8.331 | 1.884 | 0 | 0 | 0 |
| a2 | 50.47 | 1121.56 | 7.615 | 1.868 | 0 | 0 | 0 |
| c | 50.49 | 1116.87 | 7.511 | 1.858 | 0 | 0 | 0 |
| a3 | 50.76 | 1120.71 | 7.563 | 1.862 | 0 | 0 | 0 |
| d | 50.49 | 1121.61 | 7.619 | 1.854 | 0 | 0 | 0 |

## Paired comparison

- b: EU IV CPU 6.07%, combined power 7.63%, swaps 1.75% versus adjacent A phase(s); inconclusive.
- c: EU IV CPU -0.38%, combined power -1.02%, swaps -0.25% versus adjacent A phase(s); inconclusive.
- d: EU IV CPU 0.08%, combined power 0.87%, swaps -0.53% versus adjacent A phase(s); inconclusive.

## Quality

- At least 15 aligned power and swap samples per phase: True
- A-phase EU IV CPU drift at most 5%: True
- A-phase combined-power drift at most 5%: True
- Global context/thread fail-open guard inactive: False
- Context switches by phase: {'a1': 0, 'b': 0, 'a2': 0, 'c': 0, 'a3': 0, 'd': 0}
- GL calls on other threads by phase: {'a1': 0, 'b': 0, 'a2': 0, 'c': 0, 'a3': 0, 'd': 0}
- Shadow-table overflow by phase: {'a1': 0, 'b': 0, 'a2': 0, 'c': 0, 'a3': 0, 'd': 0}
- Family suppression engaged: {'b': False, 'c': False, 'd': False}
- Visible rendering defect reported: None
- Save file changed after capture; accepted for this analysis: True
- D has only a preceding A control; treat its effect as less certain.
- Powermetrics CPU/GPU watts are system-wide estimates; swaps are an in-process frame proxy.
