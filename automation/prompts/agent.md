Work only in this workspace.

Keep `progress.md` updated when the objective, findings, blockers, test results, or next step change. Do not rewrite it after every tool call. Include:

- the current objective
- findings that are established
- open hypotheses and blockers
- the latest important test results
- the next intended action, under a heading that starts with "Next"

Saying that the task is done does not finish it. An external check decides that.

When the objective replaces an OpenGL graphics call with Metal, do not treat a similarly named function as the same operation.

## Live original capture

Only use the live commands when the campaign objective explicitly authorizes
live observation. Never launch EU IV directly or construct your own injection
sequence.

- Manual capture: `python3 -m automation.live_gfx capture ... --authorize-live`.
- Campaign capture: `python3 -m automation.live_gfx capture ... --campaign-run-root <run-root>`.
  The campaign runner owns the launch count. Do not edit `live-launch.json`,
  `live-launch.lock`, `state.json`, or `campaign.lock`.
- If capture refuses because the launch count is exhausted or busy, stop and
  report `BLOCKER: live capture budget exhausted`. Do not retry.