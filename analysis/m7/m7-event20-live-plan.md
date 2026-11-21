# M7 event-20 definitive live pilot — prepared, not authorized

Date: 21 November 2026. Status: **plan only; no EU4 launch is authorized by
this document.** Task 8 requires Tasks 1–7 to pass first.

## Preconditions

- Trusted candidate execution and immutable reference snapshot verified.
- Replay budget accounting tests pass.
- Focused suites and full offline gate pass at the final head.
- A genuine native DSH multi-round integration has succeeded with a controlled
  correction and checker acceptance.
- Independent review of the final PR head has no blocking findings.

At the time of writing, native multi-round integration (Task 5) is the open
blocker. A launch before that gate passes would violate the operator contract.

## One bounded launch

- EU4 launches: 1
- Newly sealed frames: 1
- Shadow executions: 1
- Offline replays: 2
- Correction evaluations: 1
- Wall clock: 1800 s
- Automatic merges: 0

Use the existing managed capture path, the BetterDisplay background screen,
the canonical candidate worktree, and the one-use launch claim. Never construct
a custom launcher. Restore `settings.txt` byte-for-byte and terminate the owned
process on every path.

## Capture

The new capture must record event 11 with the implemented R32F observation
path; the old seal cannot establish event-20 equivalence without it.

```sh
EU4_CAMPAIGN_STATE=<run-state> \
python3.14 -m automation.m6_frame capture \
  --output <evidence>/live \
  --campaign-run-root <run-root> \
  --callers 0x10d52fc,0x1486ad9 --min-draws 8 --max-draws 512 \
  --duration 120 --timeout 300 \
  --prefix-end 20 --prefix-max-bytes 700000000 --prefix-max-ns 10000000000 \
  --seal --shadow --background \
  --shadow-timeout-ms 360000 --shadow-wait-ms 360000 --seal-timeout-ms 600000
```

## Trusted evidence and acceptance

1. Admit the sealed capture only after `m6_frame.load_seal` and
   `load_trace(verify_blobs=True)` succeed.
2. Copy the sealed frame to a trusted root outside the candidate workspace,
   write and verify `trusted-evidence.json`.
3. The independent checker derives the request, builds and executes the
   candidate renderer, and recomputes every comparison.
4. For every event 0..20 verify event identity, target/resource identity, full
   applicable payload, candidate revision and exact differences.
5. R32F is compared bitwise; RGB8 is RGB-scope; default/RGBA8 are RGBA-scope.
6. Stop at the first mismatch. `EVENT20_EXACT` only when every required event
   is valid, applicable and exact, with no exclusions.
7. Otherwise preserve diagnostics and report `requires_additional_evidence`.

No comparison criterion may be relaxed to accommodate event 11 or any other
operation.
