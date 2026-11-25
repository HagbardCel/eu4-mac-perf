# Oracle v2 blueprint provenance

The 16 October 2026 blueprint arrived as an untracked local handoff. Its first
Git-backed identity is commit `56207cc3a2bfc28e6bb3719c04125ed42f5b0a97`
under `docs/proposals/EU4_Oracle_v2/`. The local and remote annotated tag
`oracle-v2-blueprint-20261016` both resolve to that commit.
[Original blueprint tree](https://github.com/HagbardCel/eu4-mac-perf/tree/56207cc3a2bfc28e6bb3719c04125ed42f5b0a97/docs/proposals/EU4_Oracle_v2) ·
[Original manifest](https://github.com/HagbardCel/eu4-mac-perf/blob/56207cc3a2bfc28e6bb3719c04125ed42f5b0a97/docs/proposals/EU4_Oracle_v2/MANIFEST.json)

The bundle contains the original design, inactive nested OpenCode examples and
the executable seed later copied to [`oracle/core.py`](../../oracle/core.py).
The [Oracle plan of record](PLAN_OF_RECORD.md) governs maintained implementation
and qualification. The [tracked-path guard](../../tooling/common/check_forbidden_tracked_paths.py)
enforces the root OpenCode restriction, with the reconstruction skill as the
sole tracked exception.

## Recovery and verification

R1a ran the legacy Oracle guard successfully before retirement and independently
restored all 14 files into scratch. The restored path set, regular-file modes
and Git blob hashes matched the pinned tree and the checkout bytes. The original
manifest separately matched all 13 payload paths, sizes and SHA-256 hashes.
Its own SHA-256 is
`0ac526289b7da3ac47dac52f044f8a26af09869687daed782cb116a4e88936b4`.
The restored prototype also matched the maintained seed byte-for-byte.

For offline inspection, extract the original paths into a fresh directory.

```sh
scratch=$(mktemp -d)
git archive 56207cc3a2bfc28e6bb3719c04125ed42f5b0a97 -- docs/proposals/EU4_Oracle_v2 |
  tar -x -C "$scratch"
git ls-tree -r 56207cc3a2bfc28e6bb3719c04125ed42f5b0a97 -- docs/proposals/EU4_Oracle_v2
```

Use the tree listing to check each restored path, mode and `git hash-object`
result. Use the original manifest to check payload sizes and SHA-256 hashes.
