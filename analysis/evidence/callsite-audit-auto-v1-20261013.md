# Callsite audit — `auto-v1-20261013` (offline, post-remediation)

Generated after PR #52 remediation (removed invalid `timeout_seconds`, classifier diagnostics, atomic publish). Prior artifacts archived to `audits.pre-remediation/`.

Command:

```bash
python3 -m reverse_engineering.callsite_audit --baseline-revision auto-v1-20261013
```

Artifacts: `.local/eu4/<sha>/exports/auto-v1-20261013/audits/` (not committed).

## Census

| Metric | Value |
|--------|------:|
| Instructions scanned | 5,895,057 |
| Call sites (unique) | 601,904 |
| Direct | 539,772 |
| Indirect | 62,132 |
| Ambiguous | 0 |
| Outside recognized function | 12 |
| P-code decode failures | 0 |

## Classifier diagnostics

| Metric | Value |
|--------|------:|
| Disagreements (pcode-only + flow-only) | 24,822 |
| P-code only (flow not call) | 0 |
| Flow only (P-code not call, usable) | 24,822 |
| Flow with P-code unavailable | 0 |
| Direct/indirect classification conflicts | 62,132 |

Interpretation: all usable-P-code “disagreements” are flow-only (Ghidra flow marks call where P-code has no CALL/CALLIND). Classification conflicts (both agree call, differ direct vs indirect) align with indirect call volume — spot-check samples in the report.

Samples are capped in `classifier_diagnostics` in `callsite-audit.json`. Manual spot-check of ~10 RVAs per bucket recommended before decompilation prioritization.

## Graph coverage

| Bucket | Count |
|--------|------:|
| Matched (≥1 edge) | 544,205 |
| Missing direct | 12 |
| Missing indirect | 57,687 |
| Missing ambiguous | 0 |
| Orphan edge callsites | 0 |

Partition: 544,205 + 12 + 57,687 + 0 = 601,904.

## Graphics (focused)

`CPdxMapBorderLayer::DrawBorders` (`0x00000000010cbb00`): 52 direct + 1 indirect call sites in body, 1 missing graph edge, unresolved RVA `0x00000000010cbbb7`.

Mandatory anchors and `_Gfx*` helpers: see `callsite-audit.json` → `graphics`.

## Notes

- Large `missing_indirect` is expected: instruction census vs unresolved targets in `call_edges.jsonl`.
- `inventory_bundle_digest` unchanged after audit.
