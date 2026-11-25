# Repository map and lifecycle policy

The audit classifies Git-tracked paths using a source snapshot, the curated
lifecycle policy and the audit tools. The generated context records the
identities of all three inputs. [Policy](../tooling/common/repo_lifecycle.json)
· [Audit CLI](../tooling/common/repo_audit.py)

## Lifecycle legend

| Label | Meaning | Archival posture |
| --- | --- | --- |
| `ACTIVE` | Maintained; consumers depend on it now | Keep in place |
| `SHARED_DEPENDENCY` | Imported/included by active code or the test suite | Preserve interfaces |
| `FROZEN_VERIFIER` | Immutable evidence or the validators that check it | Never move; re-verify on change |
| `RETIRED_EXPERIMENT` | Historically retired; no *proof* it is unused | **Not** automatically safe to move |
| `GENERATED_DERIVED` | Regenerable output | Keep as evidence; regenerate, do not hand-edit |
| `UNKNOWN` | Not yet classified | Do not delete or move until classified |

Classification precedence is `explicit override → curated rule → consumer signal → UNKNOWN`.
A positively detected consumer reference (import / include / CI ref) always blocks archival
eligibility regardless of which rule produced the label — classification precedence cannot
conceal contradictory evidence.

## Archival policy

The policy records three decisions.

| Property | Meaning |
| --- | --- |
| `lifecycle` | Historical/operational classification |
| `archive_candidate` | Worth investigating for archival (retired/generated, no conflict) |
| `archive_eligible` | **Explicitly** approved for archival |

The current policy starts with an empty `approved_for_archive` list. An
archival decision requires explicit approval and review of consumers,
including dynamic dependencies, scripts and operator procedures.

## Evidence qualification (curated)

Two columns in the generated `repo-lifecycle.csv` describe preserved evidence.

| `evidence_qualification` | Meaning |
| --- | --- |
| `seal_descriptor` | The seal record itself (`raw_evidence.json`); describes the capsule. |
| `verified_sealed_evidence` | A member of a capsule whose seal is asserted under a named `verification_profile`. |
| `preserved_historical_evidence` | Evidence kept as provenance without an asserted seal. |

`verification_profile` names the gate that asserts the seal (e.g. `intrusive_complete_v1`).
Run the named evidence gate to verify the bytes after an artifact changes.

## Generate an audit

Use a disposable worktree, since `--write` creates four files and updates
the marked section below. Run these commands from the repository root.

```sh
git worktree add --detach /tmp/eu4-repo-audit HEAD
cd /tmp/eu4-repo-audit
python3.14 -m tooling.common.repo_audit --ref HEAD --write
python3.14 -m tooling.common.repo_audit --ref HEAD --check
```

## Recover the historical P1 audit

The publication commit is `6d539a80c3a18fc352f5fc35d237c26fb01b6c06`.
Its context identifies source commit
`8033e35de6b6bb689c6affb9498eae0c166c5635`, tree
`e00e1b0e88e3a10c1db6f9874fbf21b791b7b620`, one policy hash and five
tool hashes. R1b checked those identities in a disposable publication
checkout and reproduced the four artifacts and the generated map section
byte for byte. The source ref spelling `8033e35` is part of the recorded
context. [Publication and statistics](https://github.com/HagbardCel/eu4-mac-perf/blob/6d539a80c3a18fc352f5fc35d237c26fb01b6c06/docs/repo-map.md)
· [Identity record](https://github.com/HagbardCel/eu4-mac-perf/blob/6d539a80c3a18fc352f5fc35d237c26fb01b6c06/docs/repo-audit-context.json)

```sh
git worktree add --detach /tmp/eu4-p1-audit 6d539a80c3a18fc352f5fc35d237c26fb01b6c06
cd /tmp/eu4-p1-audit
python3.14 -m tooling.common.repo_audit --ref 8033e35 --check
```

Use `git show 6d539a80:<path>` to recover an individual file.

| Retired file | SHA-256 |
| --- | --- |
| `docs/repo-audit-context.json` | `adf650b2a901a2cb387c94f2d5d3c5848c52d07825b1ac1d0de52dfffacf03f8` |
| `docs/repo-edges.csv` | `6ef8a8590e60a8dfc5714ad2e2e222ba0f0119916d330db83b0cb18d6138e9a6` |
| `docs/repo-inventory.csv` | `d1a023450a70c8d6b58017a3501d9e0eff8f9bfbf8bfe40a6a7fbac30eda86ae` |
| `docs/repo-lifecycle.csv` | `9f22a934eb5eeb6b299aeb635abca2916cdeb2fd27f1ee96826df49f08277625` |

<!-- BEGIN GENERATED: repo-inventory -->

<!-- END GENERATED: repo-inventory -->
