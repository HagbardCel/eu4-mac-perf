# Final independent review — PR #279 integration head

Date: 21 November 2026 (Europe/Berlin).

## Reviewed head

`283c3e12fd4943b022fcd3a30ec78432f4ed24b2`

Base: `main` `1ebaddd640f5f2cec6d137de1b130b9e3ba59f66`.
Branch: `m7/final-integration` (PR #279).

This file is the attestation commit; it adds only this review record, the
earned `qualified=true` authority flag with its `qualified_by` text, two
texture-level refusal tests, and documentation wording. It does not change the
reviewed execution path: the renderer, native executor, replay-input verifier
and receipt signer digests are identical to `283c3e12`.

## Verdicts

| Reviewer | Scope | Verdict |
| --- | --- | --- |
| `review-fixes` (independent DSH subagent) | The three original Codex threads, drawable equivalence, Ed25519, decision boundaries, the reproducible native pin, the receipt boundary | **APPROVE** |
| `review-integrated` (independent DSH subagent) | The full integrated head: event/blob integrity, pre-draw provenance, reference isolation, shader/uniform/geometry/sampler fidelity, FBO precision, receipt/registry bindings, recomputed comparison/attribution, crash/timeout, regression direction | **APPROVE** |

Both reviewers were given the exact head and worked read-only. Neither
modified tracked files and neither launched EU4.

## Original #279 review threads

| Thread | Verdict | Evidence |
| --- | --- | --- |
| A. Full-clear scissor coverage (`gfx_gl_replay_input`, `gfx_frame_graph`) | **Resolved** | Endpoint coverage (`x+w`, `y+h`), integer-only boxes, partial depth/stencil clears stay unproven; 70 frame-graph and 101 qualification tests |
| B. Front/back polygon modes (`gfx_gl_control_renderer`, `eu4_gfx_draw_control.c`) | **Resolved** | Separate `glPolygonMode(GL_FRONT/GL_BACK)`, unknown/missing modes fail closed, back-facing regression fixture |
| C. Sampler aliasing | **Resolved** | One `sampler <name> <unit>` line per active uniform, deduplicated uploads retained, two-uniform/one-unit fixture exact; arrays/level-less/compressed-expected cases refuse |

## Independent verification highlights

- Real seal 90825686: `derive_evidence` → `status=missing`,
  `drawable_equivalence=unproven`; `qualify_sealed` → `unqualified`,
  `metal_blame_allowed=false`, no renderer package created.
- Foreign pre-draw seed session/frame refused; malformed nested capture
  records and non-finite values return structured refusals; mixed-type receipt
  components return `unqualified`, not a traceback.
- Native executor rebuilt from the documented deterministic recipe from four
  different working directories: all `254618ef8ab232fd1b3c42e6186d642702e714e70d79c92f6d2d908cfa3abc7a`,
  equal to the registry component.
- Registry digests at `283c3e12`: renderer `19f819b4…`, replay-input
  `6c93974f…`, receipt-signer `52e03dd0…`, native `254618ef…`; the `.local`
  Ed25519 seed derives to the registered public key `b129e1bc…`.
- No path to `metal_blame_allowed=true` without an authenticated exact control
  and proven drawable identity was found. The `format_identity` producer is
  still absent, so real qualification remains the scientific stop owned by
  #278; the authority flag does not bypass it.

## Test evidence

| Selection | Result |
| --- | --- |
| `review-fixes` focused (Ed25519, frame graph, qualification, draw control) | 206 ran / 206 executed / 0 skipped / 0 failures |
| `review-fixes` neighbours | 303 ran / 302 executed / 1 skipped / 0 failures |
| `review-integrated` focused | 311 ran / 311 executed / 0 skipped / 0 failures |
| Reconciliation focused run (`post-r1-fixes`) | 427 ran / 426 executed / 1 skipped / 0 failures |
| Full offline gate on the final attestation head | recorded in `docs/status.md` after the run; this file is not a substitute for it |

## Non-blocking residuals

- `.local/m7/authority/eu4_gfx_draw_control` is still the pre-fix binary; it
  fails closed against the new pin. Rebuild it with the documented recipe
  before any real control run.
- Sampler arrays (including size 1) are conservatively refused because the
  capture does not record their element bindings.
- Package contents are not re-derived from the seal during attribution; the
  registered signing key remains the documented trust boundary.
- `format_identity` has no producer yet; #278 owns it.
- The frame-graph `has_clear` boundary flag is diagnostic only and still
  treats a clipped clear as superseding the prior-frame seed.
