# Intrusive diagnostic salvage (TAIL only)

- Status: **usable_ordinal_hint**
- TAIL salvage produced ordinal hints; treat as non-quantitative
- Salvage detail rows: **709371**
- Dropped profiler records (run): **191966**

## C1 vs C2 aggregate counters

- draws: relative spread 0.0002
- state_calls: relative spread 0.0011
- uniform_calls: relative spread 0.0014
- texture_binds: relative spread 0.0008
- buffer_binds: relative spread 0.0010
- program_switches: relative spread 0.0018

## TAIL window retention

- Window **24**: median r_S=0.8225095640646984, r_U=0.7376801750811259, detail_rows=355273
- Window **25**: median r_S=0.8040093489459179, r_U=0.7332654139310241, detail_rows=354098

## Rank stability

- Top-16 overlap between windows [24, 25]: **1.0**

Ordinal callsite ranks from TAIL windows only; C1/C2 provide aggregate F-counter stability. U/u program/location fields are not trusted under flag 2048.
