# F1 shadow worker — acceptance code-path review

Black-box acceptance tests cannot prove the supervisor never buffers unbounded
nested subprocess output while `replay_capture()` / `run_harness()` use
`capture_output=True`.

**Merge requirement:** `oracle.shadow.run_owned` must not leave an unbounded
`subprocess.run(..., capture_output=True)` path on the flood/replay path.
Acceptable patterns: stream with a hard cap, truncate before retention, or
terminate the child before reading unbounded stdout.

This note records the reviewer obligation; implementation must cite the
supervisor module and how nested output is bounded.
