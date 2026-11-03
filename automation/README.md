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
- `POST /api/experimental/session/{id}/wait` returned `204` while the prompt was still in the inbox. A 204 is not settlement. The adapter polls it with a 1 second timeout so history can be read while a turn is running.
- `GET /api/session/{id}/message` is paginated with `cursor.next`.

A turn is settled only when the submission id is absent from the inbox, the session is not active, no permission request is pending, and a later `idle` message has outcome `succeeded`, `failed`, or `interrupted`. The submission id is written to `state.json` before the prompt request.

`max_seconds` is active runner time. It does not include time spent while the process is stopped.

## What isolation is not

The session is created with a permission ruleset: deny the campaign directory by its absolute prefix, allow relative `read` and `edit` (`**`), and allow `bash` for `*`. That is not a filesystem sandbox. The agent runs as the same OS user as the runner and can edit the checker, the state file, or the checker stage file.

The checker digest is the SHA-256 of `accept.py` stored when the campaign state file is first created. Every later run compares against that digest before and after the checker. A mismatch pauses the campaign. This catches an accidental edit. It does not protect validation against a hostile agent.

On the first live session, a ruleset that denied `read` of `*` rejected `read` of `progress.md` and `marker.txt` (`Permission denied: read`). The agent then used the shell. The committed ruleset allows those relative reads. No live call showed a denied path outside the workspace.

## Live session

`integration: verified` against OpenCode 2.2.5 and `opencode/big-pickle` on 3 Nov 2026.

The tracked campaign [campaigns/automation-smoke/campaign.json](../campaigns/automation-smoke/campaign.json) completed in two checker rounds. The first returned `fail` because the checker always fails once. The second returned `pass` after `marker.txt` was `ready`. The recorded model was `opencode/big-pickle`. That run used an earlier ruleset that denied relative `read` calls.

A second campaign with the same checker and the committed ruleset was killed while its first turn was still executing. State still held the original submission id and session id, with status `running`. `resume` reattached to that session, did not create another one, and finished on the second checker round. The trace has both `tool.started` and `tool.completed` for the same shell call. Workspace `read` calls succeeded under the committed ruleset.

Earlier probes of `llama.cpp/unsloth/Qwen3.6-27B-MTP-GGUF:Q6_K` returned `Model unavailable`, and `opencode/pulse-alpha-free` returned `provider.auth` (not available in this country). `opencode/big-pickle` also returned `provider.free-limit` on one probe before the successful runs.
