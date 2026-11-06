# Progress

## Objective

Phase-1 pending clear/SetRT nonfatal + deferred SetRT recovery on `5b5545e`.

## Findings

- Same-gen pending + phase 1 must check transition before `UNSUPPORTED`.
- Skipped SetRT must defer viewport/`+0x78` until commit.

## Test results

`campaigns/eu4-p511-phase1/accept.py` → `pass` (`MTL_DEBUG_LAYER=1`; hidpi + AppKit + gfx suite).

## Next

Commit F; re-land via revert of `cb72abe` + cherry-pick F.
