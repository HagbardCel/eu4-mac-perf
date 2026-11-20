# D1 physical-work measurement (A and B pinned windows)

Date: 2026-11-20. Branch: `fix/harness-d1-physical-work` (PR #251).

## Window identities

| Window | Source | Identity | Decompressed SHA-256 |
|---|---|---|---|
| A | `0e6ae30c-54a4-4c33-bd24-20e86c9663af` session archive | complete export, 1,626 records | `d0daf3d5772b5855e0388b682df7615264af7f7f93b88d00524e3c9673ee2c25` |
| B | `3dc697da-68ac-45e3-8579-bdf3814a7ec3` session archive | header through **seq 1555** (turns 1–2) | `17c4e6fea6b3dcf270e8814087771336b57ec6ca51c00d3c6aedf846025c14d9` (1,557 records) |

The A hash equals the SHA-256 recorded in `plans/harness-review/assessment.md`.

## Exact commands

Baseline (`main` analyzer, before this change):

```sh
git show main:automation/dsh_efficiency.py > /tmp/d1/base_dsh_efficiency.py
python3.14 /tmp/d1/base_dsh_efficiency.py analyze /tmp/d1/A.jsonl
python3.14 /tmp/d1/base_dsh_efficiency.py analyze /tmp/d1/B_1555.jsonl
```

Candidate (this branch):

```sh
python3.14 -m automation.dsh_efficiency analyze /tmp/d1/A.jsonl
python3.14 -m automation.dsh_efficiency analyze /tmp/d1/B_1555.jsonl
```

The B window was materialized reproducibly (the analyzer also accepts the
`.jsonl.zstd` archive directly with `--max-seq 1555`):

```sh
python3.14 -m automation.dsh_efficiency analyze \
  "$HOME/.local/share/eu4-dsh/sessions/--Users-fabian-Projects-MiniProjects-eu4-mac-perf-wt_infra-.local-dsh-m7-launcher-smoke-20261119-workspace--/session-3dc697da-68ac-45e3-8579-bdf3814a7ec3/session.v4.jsonl.zstd" \
  --max-seq 1555
```

The same command without `--max-seq` reads the full archive. The JSONL split
used for the baseline above is:

```sh
python3.14 - <<'PY'
import json
rows = [json.loads(line) for line in open('/tmp/d1/B.jsonl')]
rows = [e for e in rows if not isinstance(e.get('seq'), int) or e['seq'] <= 1555]
with open('/tmp/d1/B_1555.jsonl', 'w') as fh:
    for event in rows:
        fh.write(json.dumps(event) + '\n')
PY
```

## Results

| Metric | A baseline | A candidate | B baseline | B candidate |
|---|---|---|---|---|
| `eu4_launches.count` (command matches) | 4 (includes `capture --help`) | 3 | 0 | 0 |
| `eu4_launches.process_starts_estimate` (legacy heuristic) | 8 | 6 | 0 | 0 |
| `physical_work.confirmed_game_launches` | n/a | **3** | n/a | **0** |
| ambiguous launch attempts | n/a | 0 | n/a | 0 |
| failed launch attempts | n/a | 0 | n/a | 0 |
| `tests.test_invocations` | 0 | 0 | **0** | **3** |
| `tests.runner_focused_invocations` | n/a | 0 | n/a | 3 |
| `tests.runner_repeated_invocations` | n/a | 0 | n/a | 0 |
| `tests.runner_tests_executed` | n/a | None | n/a | None |
| `service.gpu_service_seconds` | n/a | **None (unknown)** | n/a | **None (unknown)** |
| user-visible wall seconds | 56,861.697 | 56,861.697 | 3,762.130 | 3,762.130 |
| tool-wait sum / union seconds | n/a | 697.141 / 697.141 | n/a | 451.107 / 450.867 |
| job-service sum / union seconds | n/a | 1,425.196 / 1,407.775 | n/a | 445.183 / 445.183 |
| combined tool+job union seconds | n/a | 1,480.729 | n/a | 450.867 |
| goal outcome / duration | n/a | blocked / 56,401.044 s | n/a | active / 3,735.041 s |

Confirmed A launches (all three joined to a guard count, a process/session
identity and a termination record):

1. `.local/m7/goal-round1/capture` — guard `eu4_launches=1`, ps PID 25295,
   launcher termination PID 25295.
2. `.local/m7/goal-round1/capture-a` — guard `eu4_launches=2` for
   `state-captures2.json`, ps PID 20416, `capture-a terminated True`.
3. `.local/m7/goal-round1/capture-b` — same guard count, capture session
   `8ee43ade-fb4e-4873-b243-0ff704b225ce`, `capture-b terminated True`.

`python3.14 -m automation.m6_frame capture --help` is not a launch attempt and
is excluded from the confirmed and command counts.

## Discrepancy with the Q6 acceptance note

Q6 and step 07 expect **two** focused runner calls in B (the review named the
commands at seq 721 and 913). The current pinned B window through seq 1555
contains **three** genuine `automation.test_runner run` invocations, all focused
on `tests.test_agent_tools`:

| seq | turn | label |
|---|---|---|
| 721 | 1 | `m7-frame-shader-fix` |
| 913 | 2 | `m7-frame-shader-fix-verify` |
| 1517 | 2 | `m7-main-verify` |

Seq 1517 is inside the pinned window (turn 2 ends at seq 1555) and its result
reports `status: passed`. The analyzer therefore reports three; the earlier
review narrative counted the first two. No de-duplication is applied because
the invocations are distinct calls with distinct labels, not repeated text.
`runner_tests_executed` is `None` for B because the retained result text does
not include the executed-test count (721 was piped to `tail -20`, 913 and 1517
project only selected fields).

No improvement is claimed for wall time, GPU time or goal duration. The
baseline and candidate wall/goal numbers are identical by construction; only
the classification and joins changed.


## Provenance

- Base: `e71c15d6` (analyzer unchanged before this PR)
- Candidate: `0d7de60c`
- B archive decompressed-prefix SHA-256: `abe86a7b4ebb3da86b70730b5bf269f85bddd27448fb3e26e5787e7a879edc82`; materialized 1,557-line window JSONL SHA-256: `17c4e6fea6b3dcf270e8814087771336b57ec6ca51c00d3c6aedf846025c14d9`.
