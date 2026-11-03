# Automation runner

One OpenCode session, an external checker, and files under `.local/automation/` (gitignored). The campaign JSON, the checker, `progress.md`, and the JSONL trace do not import OpenCode.

```sh
OPENCODE_URL=http://127.0.0.1:4096 \
OPENCODE_SERVER_PASSWORD=... \
python3 -m automation.cli run campaigns/automation-smoke/campaign.json
python3 -m automation.cli resume campaigns/automation-smoke/campaign.json
python3 -m automation.cli status campaigns/automation-smoke/campaign.json
python3 -m automation.cli report campaigns/automation-smoke/campaign.json
```

`run` creates or continues. `resume` fails when there is no state file. The server must already be running on a loopback address (`opencode serve --hostname 127.0.0.1`). Basic auth uses `OPENCODE_SERVER_USERNAME` (default `opencode`) and `OPENCODE_SERVER_PASSWORD`.

Offline tests, with no model and no server:

```sh
PYTHONPATH=benchmark:. python3 -m unittest discover -s tests -p 'test_automation_*.py'
```

## Verified server

Preflighted against the installed binary `opencode v2.2.5`. Its OpenAPI is `GET /openapi.json`. There is no JSON health route; `GET /global/health` is the web UI. This adapter only speaks the V2 routes below, and only when `opencode_version` in the campaign file is `2.2.5`.

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

`integration: verified` against OpenCode 2.2.5 and `opencode/big-pickle` on 3 Nov 2026.

The tracked campaign [campaigns/automation-smoke/campaign.json](../campaigns/automation-smoke/campaign.json) completed in two checker rounds. The first returned `fail` because the checker always fails once. The second returned `pass` after `marker.txt` was `ready`. The recorded model was `opencode/big-pickle`. That run used an earlier ruleset that denied relative `read` calls.

A second campaign with the same checker and the committed ruleset was killed while its first turn was still executing. State still held the original submission id and session id, with status `running`. `resume` reattached to that session, did not create another one, and finished on the second checker round. That trace has both `tool.started` and `tool.completed` for the same shell call because history was polled during the turn. This revision reads history when the turn can settle, so a fast tool may be recorded only as `tool.completed`. The report counts that call. Workspace `read` calls succeeded under the committed ruleset. The live sessions were not rerun after this revision.

Earlier probes of `llama.cpp/unsloth/Qwen3.6-27B-MTP-GGUF:Q6_K` returned `Model unavailable`, and `opencode/pulse-alpha-free` returned `provider.auth` (not available in this country). `opencode/big-pickle` also returned `provider.free-limit` on one probe before the successful runs.
