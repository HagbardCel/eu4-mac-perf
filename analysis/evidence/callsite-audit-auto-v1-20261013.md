# Callsite audit — `auto-v1-20261013` (offline, post-`isComputed` fix)

After PR #52 remediation and `FlowType.isComputed()` fix in `ExportCallsites.java`. Prior census archived to `audits.pre-iscomputed-fix/` (earlier remediation: `audits.pre-remediation/`).

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
| Direct | 539,594 |
| Indirect | 62,310 |
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
| Direct/indirect classification conflicts | 0 |

The 24,822 flow-only callsites are **Ghidra-semantic callsites** (flow ∪ default P-code union): they include jump instructions Ghidra reclassifies with call-related flow overrides, not only machine `CALL` opcodes. That is appropriate for census vs Ghidra’s call graph; it matters when interpreting graphics dispatch at the instruction level.

Samples are capped in `classifier_diagnostics` in `callsite-audit.json`.

## Invariant regression vs `audits.pre-iscomputed-fix`

| Metric | Pre-fix | Post-fix | Notes |
|--------|--------:|---------:|-------|
| Total callsites | 601,904 | 601,904 | OK |
| Flow-only / pcode-only | 24,822 / 0 | 24,822 / 0 | OK |
| Instructions scanned | 5,895,057 | 5,895,057 | OK |
| Graph: matched | 544,205 | 544,205 | OK |
| Graph: missing direct | 12 | 12 | OK |
| Graph: missing indirect | 57,687 | 57,687 | OK |
| Orphan edges | 0 | 0 | OK |
| Classification conflicts | 62,132 | 0 | Expected drop (`isIndirect` → `isComputed`) |
| Direct / indirect (census) | 539,772 / 62,132 | 539,594 / 62,310 | +178 indirect |

The direct/indirect census shift (+178) is expected for **flow-only** rows: when raw P-code has no `CALL`/`CALLIND`, `instruction_class` falls back to flow (`isComputed()`), so corrected flow semantics change bucket labels without changing total callsite count or graph partition.

P-code-tagged indirect volume (rows with `pcode_kind == CALLIND`) is unchanged at export time; the census headline indirect count includes flow-fallback labeling.

## Flow-only spot-check (10 sample RVAs)

Manual Ghidra inspection on `classifier_diagnostics.flow_only_rvas` (EU IV project `EU4_b3d38876`):

| RVA | Mnemonic | `getFlowOverride()` | `getPcode()` | `getPcode(true)` |
|-----|----------|---------------------|--------------|------------------|
| `0x0000000000009dfb` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x0000000000009ed8` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x0000000000009ede` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x0000000000009eed` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a0ad` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a41a` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a430` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a444` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a573` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |
| `0x000000000000a976` | JMP | CALL_RETURN | BRANCH | CALL, RETURN |

All ten inspected samples were explained by `CALL_RETURN` flow overrides: disassembly is `JMP`, default P-code is a branch, and `getPcode(true)` injects call/return semantics. This does not establish the proportion across all 24,822 flow-only callsites; we do not treat the full bucket as individually validated. The census remains the union of Ghidra’s call classifiers.

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

## Deferred call-graph exporter work

[`ImportAndInventory.java`](reverse_engineering/ghidra/ImportAndInventory.java) still uses `FlowType.isIndirect()` for zero-reference indirect fallback and skips edges when references exist but none are call-type. Not changed in 1A; future call-graph hardening.

## Notes

- Large `missing_indirect` is expected: instruction census vs unresolved targets in `call_edges.jsonl`.
- `inventory_bundle_digest` unchanged after audit.
