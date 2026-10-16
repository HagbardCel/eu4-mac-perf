# Oracle v2 blueprint — preservation provenance

**Status:** historical preservation record. This documents the *original delivered
blueprint bundle*; it does not authorize or describe new work. The authoritative
specification of the capture/validation infrastructure remains
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) and the documents named under *Ownership* in
[README.md](README.md).

## Origin

| Field | Value |
| --- | --- |
| Provenance | Local, **untracked** handoff — the bundle was delivered on disk and had no Git source revision before this record. The preceding `main` revision identifies the repository state at handoff only; it does **not** establish the historical identity of these files. |
| Handoff date | 2026-10-16 |
| First Git-backed identity | The preservation commit created together with this file; this is the bundle's first tracked revision. |
| Annotated tag | `oracle-v2-blueprint-20261016` (points at the preservation commit). The tag's resolved commit is pinned independently by `tooling/common/check_oracle_bundle.py`; it is not embedded here to avoid a self-referential identity. |
| Preserved location | `docs/proposals/EU4_Oracle_v2/` (verbatim; not relocated) |

The frozen P0 baseline that this handoff predates is recorded in
[BASELINE_P0.md](BASELINE_P0.md).

## Preserved files

Hashes below were computed independently from the on-disk bytes at handoff. They
match `MANIFEST.json` (kind `eu4_oracle_v2_blueprint`, status `offline design, NOT runtime capture`).

| Path | Bytes | SHA-256 |
| --- | ---: | --- |
| `README.md` | 1800 | `4bb28dd8be12d3bf8e5ea0c0632c5d235b7a1a38a549bf43eefcdc74572d67d2` |
| `design/MASTER_DESIGN_V2.md` | 22686 | `2ec8ec0e19e91e4f9c345efdb9854e800e73adb8dc44db8dd0aa70ac2dfe6325` |
| `design/OPENCODE_FEASIBILITY.md` | 4987 | `26dedfd34e65b31294255b66d9ce0defb0254a6f261930e598815911c4b968bb` |
| `design/PR_CHECKLIST.md` | 4543 | `c940dfe6a2d5d1fddac22efcfd3dd0798ff57b89c62048734d4b9d8e5768943b` |
| `examples/.opencode/agents/eu4-reconstructor.md` | 1498 | `95712a8bdabad187f66fefd67c29ce3d8e4c16603a8323f3bcdbddc996606432` |
| `examples/.opencode/tools/oracle.ts` | 2876 | `5f46f0ffa2cd581ca5f1cd6c318c08ef4623f47ccedb591c1852bad900d23a9f` |
| `examples/CONTROLLER_SKETCH.ts` | 1126 | `ce077254058fe9f21d15978929132e1df3bbaaeca05dfd6df621023548fbe712` |
| `examples/diagnostic.example.json` | 635 | `53b182487fcb75c97bc410bd5ff859e318f6b713c7625cec463b289ec371b337` |
| `examples/diagnostic.schema.json` | 1582 | `a432637828ece15a531e4582aeaecbe5f0bad8a5e3cacc478ab6d7034f74b0cf` |
| `examples/opencode.json` | 451 | `6d9e8622fce35f2433d76729205026dfee9895640fe15ed24246ecaf4639d5c8` |
| `oracle_proto/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `oracle_proto/core.py` | 5214 | `465a7b336cdc7524181ca5995b4f198b645664b8f890cfe8e1791e02e19db56d` |
| `tests/test_core.py` | 3039 | `247884615fef57bb93028b26c23274a3f611fc2367c26ebe874643d3e1f75998` |
| `MANIFEST.json` (not self-listed) | 2202 | `0ac526289b7da3ac47dac52f044f8a26af09869687daed782cb116a4e88936b4` |

## Historical, not authority

- The blueprint's design prose (`design/MASTER_DESIGN_V2.md`, `design/OPENCODE_FEASIBILITY.md`,
  `design/PR_CHECKLIST.md`) is retained as delivered reference. It is **not** a competing
  architecture authority; binding decisions live in the plan of record.
- `examples/` contains **inactive** OpenCode templates. They are nested under
  `examples/.opencode/` and are never loaded as project configuration; no root `.opencode/`
  is activated by this preservation.
- The executable prototype (`oracle_proto/core.py`, `tests/test_core.py`) is the *seed*
  migrated to a living package during P2; the copies here remain verbatim. It is an offline
  contract demonstration, not a runtime recorder, ABI emulator, or secure sandbox.

## Verification

`tooling/common/check_oracle_bundle.py` enforces two independent relationships: current
bytes match `MANIFEST.json`, **and** current bytes match the Git objects at the pinned
preservation commit. It fails on missing files, unexpected files, content mismatch and
symlinks. Run in CI.
