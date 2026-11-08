# BuildGLSL executable model (Simple slice)

Provenance tags: proposed observations are `reconstructed_model` until promoted
via `instruction_static` or `original_execution`.

## Escalation policy

1. Inspect dependency graph (`BuildGLSL` → Defines / Samplers / ConstantBuffers /
   VertexStructs / MainCode; vendor via `glGetString(GL_VENDOR)`).
2. Attempt smallest original-code harness (not live EU4).
3. Record every substituted dependency.
4. If isolation is unjustifiably complex → remain on `instruction_static` for
   forced fragments only.
5. If required source identity cannot be established → `BLOCKER:` with the exact
   unproven dependency.

## Current status

- Asset MainCode for Simple is observed on the authorized install
  (`simple.shader` sha256 `b40fd850…`).
- Byte-identical `BuildGLSL` compiler inputs for the loading-screen capability
  domain are **not** yet original-execution qualified.
- Do not admit substring-matched sources in P5.2b until
  `capability-domain.json` lists exact `compiler_input_sha256` values.

## Identities (never conflate)

- `repo_baseline_commit`: `173321b6d3258576060e7941e9bd91f608e8f543`
- `eu4_executable_sha256`: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
