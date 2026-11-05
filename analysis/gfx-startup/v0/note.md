# gfx-startup/v0 — integrity-fixed authorized OpenGL capture

Local raw result (not committed): `results/20261105T225108Z-gfx-startup/`

| Artifact | SHA-256 | Bytes |
| --- | --- | --- |
| `events.jsonl` | `2282329b5e8604342938285ae3a05105ad64d58eb4f078ee39d926039a55788c` | 1859 |
| `summary.json` | `3a09ce8073b7010a318d87e8a3b5eaebcbba6a819f00638a7e9b3238bad0c82a` | 248 |
| `metadata.json` | `0642a06d076731d7597957e2d8b6a0a7af4c96908acc9e51168e7f428b3190f0` | 282 |
| `startup.status` | `9ec9c802810554b66005044fd2fd1c5ed274abfdf5edec6ac39b0ba256c34f84` | 328 |

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Launcher pid `41350` matched `startup.status`.

Summary: `complete=true`, `settings=observed` (1 call), `qualifying_init=1`, `init_calls=1`, four events (`seq` 1–4), `events_copied=true`, no overflow/unmatched/unavailable/in_flight. Settings entry: `sample_count=15`, `sample_captured=15`, `sample_values`/`sample_flags` length 15. Original OpenGL only; Metal was not injected.

Integrity: early `in_flight` admit with `seq_cst` recheck, fail-closed null/oversize settings, exclusive `IDLE→WRITING→DONE` finalize with SEALED-only event copy and summary rename-last.
