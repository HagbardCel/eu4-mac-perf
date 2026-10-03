# WP10 — bounded remediation diagnostic

Single Mac capture to answer **two** post-`a73ea57b` questions without requalification authority:

1. **Mesh counters CPU** — does a test-only `counters-lite` path (no per-frame scope `Q` tree; optional deferred `flush_counters`) materially cut overhead?
2. **text_ui completion wall** — is variance fixable with more 40-frame trials or longer (400-frame) windows?

v3 training requalification **failed**; **held-out remains blocked**. This work package is **diagnostic only**.

## Mac capture

On a **clean** tree:

```bash
python3 benchmark/eu4_frame_model.py wp10-bounded-remediation --registry-json
```

Register under **`bounded_remediation_archives`** (`work_package: WP10`). Do not treat results as Tier-1 admission.

## Protocol

**Mesh CPU** (`prime=1`, `measured=4`, 7 trials, rotated stage order):

```text
reference → counters → counters_lite → counters_lite_deferred_flush
```

Harness env (test library only):

- `EU4_TEST_COUNTERS_LITE=1` — skip scope tree; keep hook/`C` accounting and frame `F` rows.
- `EU4_TEST_COUNTERS_DEFERRED_FLUSH=1` — with lite; defer per-frame `flush_counters()` until workload end.

Pre-specified engineering success (not a policy gate): **median counters-minus-reference CPU ≤ 5 µs/frame** on mesh (~50% reduction from ~9.6 µs in `a73ea57b`).

**text_ui completion** (`prime=1`, completion timing on all stages):

| Variant | Trials | Measured frames |
|---------|--------|-----------------|
| `forty_frame_21_trials` | 21 | 40 |
| `four_hundred_frame_7_trials` | 7 | 400 |

Compare completion-wall CIs in the archive; tri-state v3 rules are replayable offline but **this capture does not assert admission**.

## After capture

- If counters-lite meets the engineering target → productionize lean steady-state counters (scopes only in forensic windows); freeze policy/implementation; **one** fresh v3 training requalification; then held-out if training passes.
- If text stabilizes only with a new window/trial contract → define **v4** (new policy version), do not reinterpret v3.
- If neither path is viable → stop extending qualification infrastructure; use profiler as diagnostic only and return to EU IV hypotheses.
