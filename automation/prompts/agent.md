Work only in this workspace.

Keep `progress.md` updated when the objective, findings, blockers, test results, or next step change. Do not rewrite it after every tool call. Include:

- the current objective
- findings that are established
- open hypotheses and blockers
- the latest important test results
- the next intended action, under a heading that starts with "Next"

Saying that the task is done does not finish it. An external check decides that.

When the objective replaces an OpenGL graphics call with Metal, do not treat a similarly named function as the same operation. In the research note, separate observed original behavior, documented Metal requirements, and deliberate adaptation.

Before editing a replacement, establish:

- Source API: accepted input ranges, coordinate conventions, state changes, error behavior, and resource lifetime.
- Destination API: Metal parameter types, preconditions, valid ranges, encoder requirements, and state lifetime. Locate the installed SDK with `xcrun --sdk macosx --show-sdk-path` and read its framework headers. Official pages may be fetched when the network is available. If a fetch fails, say so; do not invent a documentation claim.
- Semantic mismatch: OpenGL inputs Metal cannot accept directly, including clipping, signedness, enablement, and resource constraints.
- Adaptation: where the translation happens and which effects stay deferred.
- Validation: boundary tests for those differences, and a real Metal call when a device is available.

The scissor example is `analysis/metal-api/scissor.md`.
