---
package: EXACT-DEDUP-1
upstream: 113e847d7e2d93f5e7c14e7e85fd03edeed7d7f1
contracts:
  - exact-dedup/v1
  - FUNCTION-CAPTURE v1 (retained transcripts only)
  - EU4-GFX-LIVE-SUMMARY v1 (historical admission, unchanged)
owned_paths:
  - docs/oracle/EXACT_DEDUP_V1.md
  - benchmark/eu4_gfx_live_capture.c
  - benchmark/eu4_gfx_live_capture.h
  - benchmark/eu4_gfx_live_capture.py
  - benchmark/eu4_gfx_live_capture_dylib.c
  - benchmark/eu4_gfx_set_constant_buffer_capture.h
  - tests/gfx_live_capture_harness.c
  - tests/test_gfx_live_capture.py
  - tests/test_gfx_live_capture_report.py
frozen_read_only:
  - docs/oracle/ACQ_DIVERSITY_V1.md
  - oracle/certify/**
  - oracle/function/**
  - docs/oracle/policies/**
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# EXACT-DEDUP-1 — reusable pool and exact dedup

Implement [EXACT_DEDUP_V1.md](../EXACT_DEDUP_V1.md) on the PR D recorder.
Do not import the ACQ-1 signature table, sampling policy, or quota feasibility
word. Do not launch EU IV. Historical v1 report tests stay strict.
