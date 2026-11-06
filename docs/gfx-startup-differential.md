# Graphics startup differential (OpenGL reference vs Metal worker)

PR3 replays a committed, pointer-free `gfx-startup-ref/v0` fixture through the existing Metal shadow worker and classifies fields under [`analysis/gfx-startup/v0/comparison-contract.md`](../analysis/gfx-startup/v0/comparison-contract.md).

No new hooks. No worker protocol changes. EU4 is not launched.

## Commands

```
# Rebuild the fixture from a local PR2 capture (hash-checked against note.md)
python3 benchmark/eu4_gfx_startup_diff.py normalize \
  --source results/20261105T225108Z-gfx-startup \
  --out analysis/gfx-startup/v0/reference.json

# Offline compare against recorded worker responses
python3 benchmark/eu4_gfx_startup_diff.py compare \
  --reference analysis/gfx-startup/v0/reference.json \
  --responses path/to/worker-responses.jsonl

# Live Metal worker replay + report (always writes the output dir)
python3 benchmark/eu4_gfx_startup_diff.py run \
  --reference analysis/gfx-startup/v0/reference.json
```

## Tests

```
python3 -m unittest tests.test_gfx_startup_diff
python3 -m unittest tests.test_gfx_startup_diff_metal   # macOS + Metal
```

## Reading classifications

| Class | Meaning |
| --- | --- |
| `matched` | Comparable values agree |
| `diverged` | Strict (or algorithm) values disagree |
| `capability_difference` | Backend/device-dependent difference |
| `not_comparable` | Reference lacks corresponding evidence |
| `abi_unresolved` | Settings return register vs worker int |
| `unavailable` | Missing observation or failed worker envelope |

`contract_pass` requires every strict gate to match, successful Metal init, valid provenance, and valid response envelopes. Capability / ABI / not_comparable rows stay visible and do not alone fail the contract.

Width/height matches are `conditioned_on_reference`: the worker was given the original dimensions.

Raw `results/<timestamp>-gfx-startup-diff/` stays local; a brief summary is in [`analysis/gfx-startup/v0/diff-note.md`](../analysis/gfx-startup/v0/diff-note.md).
