# Paused GOG EU IV draw screening

**Formal decision: inconclusive.** The collector stopped after one of three planned windows, and the active trace exceeded the 5% intrusion gate.

Captured 186,179 draws across 1 window; 31 complete frames each contain 5,863 draws. Known caller coverage: 99.4%. Overflow/bad-state flags: 0/0.

Baseline: 52.54 swaps/s; trace: 47.48 swaps/s.

| Draw source | Draws | Share |
|---|---:|---:|
| mesh_object | 88,768 | 47.7% |
| borders | 71,818 | 38.6% |
| map_text | 12,192 | 6.5% |
| ui_or_text | 8,032 | 4.3% |
| lakes_and_rivers | 3,658 | 2.0% |
| unknown | 1,088 | 0.6% |
| terrain | 465 | 0.2% |
| postprocess | 158 | 0.1% |

| Aggregation screen | Adjacent pairs | Share of draws |
|---|---:|---:|
| Same tracked state, indexed multi-draw | 60,290 | 32.4% |
| Strictly contiguous index ranges | 0 | 0.0% |
| Repeated mesh geometry, changed uniforms | 11,296 | 6.1% |

The multi-draw candidates break down as:

- borders: 48,418 adjacent pairs
- map_text: 11,744 adjacent pairs
- mesh_object: 128 adjacent pairs

This is an upper-bound screen based on tracked GL state. It does not establish full command equivalence, visual correctness, or a performance gain. In particular, object constants, ordering, and untracked GL state need a narrow prototype check.

**Next experiment:** prototype multi-draw consolidation for the border path first, then map text if the border result is promising. The data do not justify a general mesh-bucket merge or instancing prototype yet. No additional diagnostic launch is needed for this screen.
