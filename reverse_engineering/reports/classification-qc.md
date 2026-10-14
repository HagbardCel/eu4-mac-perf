# Renderer classification QC (bounded manual review)

Complete this after the local Ghidra census run. Record RVA + short rationale only—no pseudocode dumps in Git.

## Established classification rules (not full-corpus QC)

| RVA | Primary | Confidence | Notes |
|-----|---------|------------|-------|
| `0x00000000010cbb00` | B2 | probable | `CPdxMapBorderLayer::DrawBorders` — Gfx draw owner per [`analysis/draw-callers.json`](../../analysis/draw-callers.json); B2 pattern includes `CPdxMapBorderLayer` (map border rendering). |

## Checklist

1. Representative spot-check per subsystem (B0–B3): at least three functions each.
2. Review top-N functions by attributed machine-code bytes (N ≥ 10).
3. Review borderline B0/B1 functions with `direct_opengl_caller` tag.
4. Note misclassifications and corrections applied to rules or overrides.

## Record

| RVA | Primary | Confidence | Notes |
|-----|---------|------------|-------|
| | | | |

## Outcome

- Confirmed byte total: _TBD after local run_
- Probable byte total: _TBD_
- Unclassified byte share: _TBD_
