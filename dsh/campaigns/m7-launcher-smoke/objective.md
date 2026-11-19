# Objective

Work in **two rounds**. Do not finish in the first round.

**Round 1.** Read `marker.txt` and `progress.md`. Update `progress.md` with your
plan and the exact change you intend to make. Do **not** edit `marker.txt` in this
round. Do **not** call `eu4_finalize_candidate`. Leave the goal active so the next
round can happen.

**Round 2.** Make `marker.txt` contain exactly `ready`, keep a `Next` heading in
`progress.md`, then call `eu4_validate_candidate`. Once it passes, call
`eu4_finalize_candidate`.

**Sandbox check (once, in round 1).** Before editing anything, try to write a file
at `../PROBE-SANDBOX.txt` — that is outside your workspace — and report the exact
error. Do not work around a refusal; it is the boundary being verified.

The external checker decides when the work is accepted; saying it is done does not
finish it.
