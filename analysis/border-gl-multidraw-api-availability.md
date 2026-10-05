# Gate 0 — multidraw API viability

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)

## Current loop-head requirement (PR #39 / border patch)

**Chosen mutation API:** [`glMultiDrawElements`](border-mode0-basevertex-evidence-addendum.md) (mode-0 border ≡ N×`glDrawElements` with zero index offsets).

**Runtime Gate 0 (mutation PR):** With the **gameplay GL context current**:

1. Resolve `glMultiDrawElements` (or engine `_glewMultiDrawElements` tail used by the game).
2. Fail closed if the symbol is missing or the call cannot be issued in the active context.

**Not required for this path:** `glMultiDrawElementsBaseVertex`, `glDrawElementsBaseVertex`, or inferring support from `GL_VERSION` ≥ 3.2 alone.

| Source | Finding |
|--------|---------|
| [frame-model-draw-api.json](frame-model-draw-api.json) | macOS OpenGL inventory lists `glMultiDrawElements` (plausibility only) |
| [border-loop-head-patch-plan.md](border-loop-head-patch-plan.md) | SysV 5-arg MDE call in trampoline design |
| Evidence JSON | `GATE0_GL_MULTIDRAW_ELEMENTS_IN_ACTIVE_CONTEXT` |

Imports / frame-model catalogs alone do **not** prove the live gameplay context exposes the entry point.

**Verdict:** **RUNTIME-ASSERT** for MDE — static RE makes the call shape valid; PR C must probe with context current before enabling mutation.

**Also runtime (not static):** `RUNTIME_DETOUR_INSTALLATION_AND_EXECUTION` — install 14-byte detour, writable code page, icache/coherence (see patch plan).

---

## Historical note — MDEBV screening (pre–MDE pivot)

Earlier border multidraw screening assumed per-draw base vertices and discussed **`glMultiDrawElementsBaseVertex`**. That rationale is **superseded** for loop-head by the mode-0 register map and [basevertex addendum](border-mode0-basevertex-evidence-addendum.md).

The following is **archival context** for Phase II draw-path screening, **not** the PR #39 loop-head Gate 0:

| Source | Finding |
|--------|---------|
| [frame-model-draw-api.json](frame-model-draw-api.json) | Symbol catalog includes `glMultiDrawElementsBaseVertex` |
| Legacy screening docs | MDEBV + varying record `+0x04` (now known non-GL on mode-0) |

Historical PR C text referenced:

1. `glDrawElementsBaseVertex` / `glMultiDrawElementsBaseVertex` resolution.
2. `GL_VERSION` ≥ 3.2 style checks.

Those checks apply to **MDEBV-based** experiments only. Do not block the **MDE** loop-head path on MDEBV availability.
