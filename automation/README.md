# Automation runner

One OpenCode session, an external checker, and files under `.local/automation/` (gitignored). The campaign JSON, the checker, `progress.md`, and the JSONL trace do not import OpenCode.

```sh
OPENCODE_URL=http://127.0.0.1:4096 \
python3 -m automation.cli run campaigns/automation-smoke/campaign.json
python3 -m automation.cli resume campaigns/automation-smoke/campaign.json
python3 -m automation.cli status campaigns/automation-smoke/campaign.json
python3 -m automation.cli report campaigns/automation-smoke/campaign.json
```

`run` creates or continues. `resume` fails when there is no state file. The server must already be running on a loopback address (`opencode serve --hostname 127.0.0.1`). The adapter sends no credentials and expects no authentication.

Offline tests, with no model and no server:

```sh
PYTHONPATH=benchmark:. python3 -m unittest discover -s tests -p 'test_automation_*.py'
```

## Generated against

The request and response shapes below were generated against the installed binary `opencode v2.2.5`, and that build is recorded in the campaign's `opencode_version`. The version is **not** enforced: any build is accepted, and a live run on a different one should be re-preflighted against its `GET /openapi.json` first. There is no JSON health route; `GET /global/health` is the web UI.

The adapter sends no credentials. Note that `opencode serve` on 2.2.5 and 2.2.6 always generates a server password and requires HTTP Basic auth, with no option to disable it. Pointing the runner at `serve` from those builds therefore returns `401` (recorded as `runtime_unavailable`); it needs a server that does not require auth.

- `POST /api/session` with `location.directory`, `model` (`providerID` + `id`), and `permissions`. The returned directory must match the live workspace or the run stops.
- `POST /api/session/{id}/prompt` with a caller-supplied `msg_…` id. A second post of the same id returned the existing admission during preflight; `409` is treated as that same id, not a new prompt.
- `GET /api/session/{id}/inbox` lists work that has been admitted and not yet delivered.
- `GET /api/session/active`, `GET /api/session/{id}/permission`.
- `GET /api/session/{id}/message` is paginated with `cursor.next`. The experimental wait route is not called. A preflight `204` arrived while the prompt was still queued, so it is not a settlement signal.

While a turn is running the runner polls permission, the active list, and the inbox once a second. It reads history only once the submission is out of the inbox and the session is inactive. Settlement reads that history twice, once to decide the turn is finished and once to record it. A later change can reuse the first read. A null `cursor.next` ends the history. A follow-up page that only repeats ids already seen, with the same cursor, is the end marker observed on 2.2.5. A cursor that changes without new ids, a cursor requested twice, or a live cursor after 100 pages pauses `history_incomplete`. If that read fails for any other reason, the campaign pauses `trace_unavailable` and does not accept the turn.

A turn is settled only when the submission id is absent from the inbox, the session is not active, no permission request is pending, and a later `idle` message has outcome `succeeded`, `failed`, or `interrupted`. The submission id is written to `state.json` before the prompt request. A retry on the same session reuses that id. A replacement session gets a new id, and its first submitted message contains the objective, the agent instructions, `progress.md`, and any pending validation feedback.

`max_seconds` is active runner time. It does not include time spent while the process is stopped.

## What isolation is not

The session is created with a permission ruleset: deny the campaign directory by its absolute prefix, allow relative `read` and `edit` (`**`), and allow `bash` for `*`. That is not a filesystem sandbox. The agent runs as the same OS user as the runner and can edit the checker, the state file, or the checker stage file.

The checker digest is the SHA-256 of `accept.py` stored when the campaign state file is first created. Every later run compares against that digest before and after the checker. A mismatch pauses the campaign. This catches an accidental edit. It does not protect validation against a hostile agent.

Checker stdout is stored and then rejected when it is over 64 KiB. That is not a streaming cap: the process can emit more before the runner notices. The trace redacts `NAME=value` secrets and values under keys such as `token`, `password`, `secret`, `api_key`, and `authorization`. A secret written only inside a free-form command can remain. The usage object under `tokens` is kept.

On the first live session, a ruleset that denied `read` of `*` rejected `read` of `progress.md` and `marker.txt` (`Permission denied: read`). The agent then used the shell. The committed ruleset allows those relative reads. No live call showed a denied path outside the workspace.

## Live session

`integration: verified` against OpenCode 2.2.5 and `opencode/big-pickle` on 3 Nov 2026. That is the build the behaviour below was observed on, not a version gate.

The tracked campaign [campaigns/automation-smoke/campaign.json](../campaigns/automation-smoke/campaign.json) completed in two checker rounds. The first returned `fail` because the checker always fails once. The second returned `pass` after `marker.txt` was `ready`. The recorded model was `opencode/big-pickle`. That run used an earlier ruleset that denied relative `read` calls.

A second campaign with the same checker and the committed ruleset was killed while its first turn was still executing. State still held the original submission id and session id, with status `running`. `resume` reattached to that session, did not create another one, and finished on the second checker round. That trace has both `tool.started` and `tool.completed` for the same shell call because history was polled during the turn. This revision reads history when the turn can settle, so a fast tool may be recorded only as `tool.completed`. The report counts that call. Workspace `read` calls succeeded under the committed ruleset. The live sessions were not rerun after this revision.

Earlier probes of `llama.cpp/unsloth/Qwen3.6-27B-MTP-GGUF:Q6_K` returned `Model unavailable`, and `opencode/pulse-alpha-free` returned `provider.auth` (not available in this country). `opencode/big-pickle` also returned `provider.free-limit` on one probe before the successful runs.

## Reconstruction checker

A gfx reconstruction campaign keeps this `campaign.json` schema and adds `reconstruction.json` with four fields: `symbol`, `note`, `allowed_paths`, and `test_modules`. Its thin `accept.py` finds this repository from its own path and calls `automation.reconstruction_accept.check`. It does not depend on `PYTHONPATH`. The checkers in `campaigns/eu4-index-buffer` and `campaigns/eu4-vertex-buffer-multiple` stay the records of those experiments.

The shared checker requires a detached worktree at the pinned commit, pinned `benchmark/eu4_gfx_entries.def` and `reverse_engineering/target.json` blobs, and its own bytes equal to `git show <pinned>:automation/reconstruction_accept.py`. There is no bootstrap exception. It stores one research checkpoint beside the worktree, allows a follow-up only after that text, and accepts a `BLOCKER:` line only when the checkpoint is absent and the edits are still the note and `progress.md`, or when an existing checkpoint is intact. Implementation then checks one new registry entry against the stored instruction span, builds the candidate with `build_library("x86_64")`, and requires each configured module to run at least one non-skipped test.

`pass` is offline qualification for independent review. `fail` and `inconclusive` still continue the campaign. `blocked` pauses `semantic_blocker` and resume does not start another turn. `unavailable` is a first-class checker verdict: the runner pauses `validation_unavailable` and does **not** open another remediation turn (environment/integrity gap, not a bad candidate). A shared-checker mismatch, a pinned-metadata or campaign-config mismatch, and a checker-owned checkpoint that cannot be read or no longer matches the campaign also map to that pause. When a campaign declares `verification_manifest`, its digest is stored in campaign state at creation and re-checked before/after accept; a member hash mismatch pauses `verification_manifest_modified`. The runner still calls `require_worktree` before the checker, so a missing worktree, a worktree from another repository, or a HEAD that is not the pin raises before the checker runs. The checker's own worktree result applies only after that preflight, including a branch that is already at the pin but not detached. A fresh worktree does not inherit untracked files. A reused worktree may still contain the previous root `progress.md`; remove that file before the first agent turn. The instruction listing, registry parser, and Metal link stay in the existing research and boot tools. Candidate test subprocesses set `MTL_DEBUG_LAYER=1` and `MTL_DEBUG_LAYER_ERROR_MODE=assert` before a Metal device is created. An OpenGL-to-Metal objective loads the `opengl-metal-reconstruction` skill only when the pinned worktree's copy matches the pinned blob. A later campaign must pin the checker revision that contains this environment; older pins stay on their recorded checker bytes.

## A0 trusted validation and handoff

- Promote grounded evidence: `python3 -m automation.promote_evidence` (writes `analysis/qualification/v0/`; golden only—no workspace candidate under the oracle tree).
- Smoke campaign: `campaigns/eu4-a01-trusted-validation/` — checker reads the **workspace** candidate at `analysis/qualification/v0/primitive-lookup.candidate.json`; oracle/manifest stay trusted and re-hashed.
- Outer loop CLI:
  - `python3 -m automation.handoff_cli run-scripted <workflow.json>` — deterministic two-defect A→B path for **portable tests** (synthetic fixture oracle under the workflow dir; not independent agent review).
  - `python3 -m automation.handoff_cli run-opencode <workflow.json> <campaign.json>` — real OpenCode implementer **and** reviewer sessions. No synthetic reviewer fallback. The adapter sends no credentials.
- Reviewer campaign: `campaigns/eu4-a02-reviewer/` — report path is `EU4_REVIEW_REPORT_PATH` **outside** the candidate worktree; accept returns `pass` when the review operation finished (any outcome). Controller consumes `qualified|rejected|unresolved`.
- Promotion is append-only and content-addressed: version directory is published before the tip manifest replace; already-applied finding digests prevent duplicate promote after crash; `CURRENT` is a convenience mirror of the tip manifest.
- Candidate snapshots are full baseline trees (`read-tree` pin + allowlisted overlay) parented on `pin_commit`.
- Qualification receipt: `(candidate_commit, oracle_digest, checker_digest, review_digest)`. Remediation campaigns seed from the rejected snapshot and pin a version-specific oracle manifest path before the first agent turn.
- Identity fields stay distinct: `repo_baseline_commit` vs `eu4_executable_sha256`.
- Portable unittest fixtures live under `tests/fixtures/qualification/`; they must not mutate tracked tip oracle files.

## Metal API corpus

The contracts live in [analysis/metal-api/](../analysis/metal-api/README.md). `index.md` is the lookup table. Search it with `rg`, for example `rg -n -i 'setScissorRect|glScissor' analysis/metal-api`. The skill [`.opencode/skills/opengl-metal-reconstruction/SKILL.md`](../.opencode/skills/opengl-metal-reconstruction/SKILL.md) tells the agent to read that index and the named contract. `agent.md` does not order the load by itself.

The runner compares the live skill file with `git show <pinned>:.opencode/skills/opengl-metal-reconstruction/SKILL.md` when it starts or resumes, before a new session or submission, and before it accepts the candidate. It does not compare on each poll. A match allows that skill id. A pin without the file, including an untracked copy on disk, denies the skill id, so a global skill of the same name is not available. If the pin contains the file and the live file is missing or its bytes differ, the run pauses `skill_mismatch` before reconciling or submitting. A saved pending submission keeps its id and text. A Git failure that is not a missing path pauses `runtime_unavailable`. A campaign with no worktree pin is the absent case. `resume` checks again after the file is restored.

`sources.json` records the SDK version, the review date, the headers read, and the Apple URLs opened for each contract. `reviewed` means those fields were filled after that reading. A sentence inside the contract is labeled `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`. The checker does not prove that a source supports a sentence.

To update a contract, re-read the header from `xcrun --sdk macosx --show-sdk-path`, re-open each URL you still cite, and set `sdk_version` and `reviewed` to what you used. A missing page or header leaves the sentence `Unresolved`. The agent reports that with the existing `BLOCKER:` line when the gap blocks a faithful replacement.

`.local/metal-docs/` is optional and is already ignored by `.local/`. The contracts do not need it. A campaign worktree sees the skill and the corpus only when its pinned commit contains them and the live file still matches that blob. Historical campaign JSON, notes, and pinned checker bytes stay as recorded. The next reconstruction campaign is the evaluation that the skill was loaded, the relevant contract was read, and a test came from a documented mismatch. This checker does not enforce that.

The session denies `opengl-metal-reconstruction` unless that pinned match succeeded. Other skills stay unmatched and would ask. Unmatched asks pause the campaign.
