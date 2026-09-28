# GOG EU IV state-cache validation

One paused, hands-off A–B–A–C–A–D launch; B=texture, C=texture+vertex, D=texture+vertex+uniform.

| Phase | Swaps/s | EU IV CPU ms/s | CPU W | GPU W | Texture skipped | Vertex skipped | Uniform skipped |
|---|---:|---:|---:|---:|---:|---:|---:|
| a1 | 51.96 | 1202.465 | 8.514 | 2.013 | 0 | 0 | 0 |
| b | 51.42 | 1102.9 | 7.559 | 1.886 | 8,611,570 | 0 | 0 |
| a2 | 51.69 | 1117.515 | 7.819 | 1.913 | 0 | 0 | 0 |
| c | 49.6 | 1108.6 | 7.585 | 1.797 | 8,276,008 | 18,981,146 | 0 |
| a3 | 51.56 | 1114.15 | 7.619 | 1.892 | 0 | 0 | 0 |
| d | 24.87 | 1078.58 | 8.678 | 0.871 | 4,164,260 | 9,552,108 | 0 |

## Paired comparison

- b: EU IV CPU -4.92%, combined power -6.94%, swaps -0.78% versus adjacent A phase(s); inconclusive.
- c: EU IV CPU -0.65%, combined power -2.47%, swaps -3.92% versus adjacent A phase(s); inconclusive.
- d: EU IV CPU -3.19%, combined power 0.27%, swaps -51.76% versus adjacent A phase(s); inconclusive.

## Quality

- At least 15 aligned power and swap samples per phase: True
- A-phase EU IV CPU drift at most 5%: False
- A-phase combined-power drift at most 5%: False
- Global context/thread fail-open guard inactive: True
- Context switches by phase: {'a1': 1852, 'b': 1770, 'a2': 1850, 'c': 1700, 'a3': 1768, 'd': 856}
- GL calls on other threads by phase: {'a1': 0, 'b': 0, 'a2': 0, 'c': 0, 'a3': 0, 'd': 0}
- Shadow-table overflow by phase: {'a1': 0, 'b': 0, 'a2': 0, 'c': 0, 'a3': 0, 'd': 0}
- Family suppression engaged: {'b': True, 'c': True, 'd': False}
- Visible rendering defect reported: False
- Save file changed after capture; accepted for this analysis: False
- D has only a preceding A control; treat its effect as less certain.
- Powermetrics CPU/GPU watts are system-wide estimates; swaps are an in-process frame proxy.
