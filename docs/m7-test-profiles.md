# M7 offline and live test profiles

The routine gate is **offline**. It never starts EU4, moves the desktop, uses
BetterDisplay/Rosetta or requires a live GPU capture. Live qualification is a
separate, explicit profile.

## Offline (default)

```sh
python3.14 -m automation.test_profiles verify --profile offline
python3.14 -m automation.test_runner run --profile offline --label focused \
  --module tests.test_agent_tools --module tests.test_m7_diagnosis
python3.14 -m automation.test_runner run --profile offline --label full
```

`--profile offline` sets `EU4_TEST_PROFILE=offline` and
`EU4_OFFLINE_ONLY=1` for the child suite. The launch guards in
`gfx_launch.spawn`, `eu4_gfx_startup_trace.run_ops`, `live_gfx probe` and the
CLI capture paths refuse a real launch while either marker is active. Tests
that replace the launch function with a fake still exercise the surrounding
wiring.

The complete offline discovery still includes native harness builds and
offscreen Metal hosts. Those are not live game evidence; they are portable
software/hardware checks that the historical suite already relies on.

### Required and classified tests

- `automation/test_profiles.MANDATORY_OFFLINE_MODULES` is the qualification
  manifest. Removing or renaming one of those modules fails
  `tests.test_test_profiles`.
- `automation.test_runner --min-tests N` makes a suite that executes fewer than
  `N` tests `incomplete`; CI uses a floor of 3,000.
- Platform/hardware skips remain explicit `skipUnless(..., reason)`:
  Darwin-only OpenGL/Metal hosts, Rosetta x86-64 execution and similar.
- The optional QBDI x86-64 release is **not mandatory**. On a Mac without it,
  the TR3 tests that need it skip with the missing-dependency reason; on a host
  with QBDI they execute normally.
- `tests.test_live_qualification` is the only live-only module. It skips with a
  reason under the offline profile.

## Live (explicit opt-in)

```sh
EU4_TEST_PROFILE=live EU4_LIVE_CASE=/path/to/verified/case \
  python3.14 -m automation.test_runner run --profile live \
  --module tests.test_live_qualification --timeout 300
```

Rules:

- `--profile live` requires an explicit module or pattern; it is never selected
  implicitly by a full-suite run.
- `EU4_LIVE_CASE` must name an already-captured, verified case. The module does
  not launch EU4 by itself.
- A real launch still needs its operator authorization/ledger and the existing
  capture timeout/cleanup/artifact retention. `EU4_TEST_PROFILE=live` only
  removes the offline refusal; it is not authorization.
- Live evidence is never promoted into the offline gate and is never run by CI.

## CI

The `Portable profiler correctness` workflow runs the manifest check and
`automation.test_runner run --profile offline --min-tests 3000`. The required
branch-protection check is `portable`. Live qualification is not a required
check because it needs a real game install; it is an explicit operator action.
