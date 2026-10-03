# WP10 — bounded remediation diagnostic

Single Mac capture to answer **two** post-`a73ea57b` questions without requalification authority:

1. **Mesh counters CPU** — does a test-only `counters-lite` path (no per-frame scope `Q` tree; optional deferred `flush_counters`) materially cut overhead?
2. **text_ui completion wall** — is variance fixable with more 40-frame trials or longer (400-frame) windows?

v3 training requalification **failed**; **held-out remains blocked**. This work package is **diagnostic only**.

## Authoritative capture

Registered **`d07347df`** @ `a8200ea` under `bounded_remediation_archives`.

**Outcome summary** (full analysis: [`wp10-evidence-memo-20261003.md`](wp10-evidence-memo-20261003.md)):

- Mesh counters-lite **failed** the ≤5 µs engineering target; paired lite−full / deferred−lite contrasts are **inconclusive** (optimize scopes/flush not justified).
- text_ui **21×40f** completion-wall CIs fall inside v3 ±5%; **7×400f** is weaker — prefer **more trials**, not longer windows.
- Next qualification step: **v4 statistical replication** — frozen policy + [`wp11-tier1-v4-requalification.md`](wp11-tier1-v4-requalification.md) (see [`tier1-v4-statistical-replication-proposal.md`](tier1-v4-statistical-replication-proposal.md)); not further profiler architecture work.

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

**text_ui completion** (`prime=1`, completion timing on all stages): **28 trials interleaved** in 7 blocks so 40-frame and 400-frame windows share the same session (avoids attributing regime shifts to window length):

```text
block 0: 40f, 40f, 40f, 400f
block 1: 400f, 40f, 40f, 40f
… (alternating) …
block 6: 40f, 40f, 40f, 400f
→ 21 × 40f + 7 × 400f total
```

The archive stores `execution_schedule` plus per-variant comparisons (`forty_frame_21_trials`, `four_hundred_frame_7_trials`). **This capture does not assert admission.**

## After `d07347df` (decision)

- **Do not** productionize counters-lite or pursue flush-path optimization for qualification.
- **Do not** change v3 6/11 µs thresholds based on WP10.
- **Proposed:** freeze **v4** with **21 paired trials** (uniform), same gates/windows as v3 → one training requalification → held-out only if pass; else stop qualification work (see v4 proposal doc).
