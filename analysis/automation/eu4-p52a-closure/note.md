# P5.2a closure — research note

Labels: `Project-derived`, `Unresolved`.

## Identities (never conflate)

- `repo_baseline_commit`: `173321b6d3258576060e7941e9bd91f608e8f543`
- `eu4_executable_sha256`: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`

## ShowBasicLoadText / GetGfxEffect / Decision 0003

Callsite draw `0xedfcd1` under `ShowBasicLoadText` loads effect index from
`CEU3Graphics+0x2c660` via `GetGfxEffect`. Primitive path passes engine enum `4`;
`GetOpenGLPrimitiveType` (`0x15edff7`) maps `table[4] → 5` (`GL_TRIANGLE_STRIP`)
per `instruction_static` evidence in
`analysis/qualification/v0/primitive-table.evidence.json`.

## Local fixtures

Authorized `simple.shader` lives under `.local/fixtures/sha256/b40fd850…` (not
redistributed in git).

## BuildGLSL

`Unresolved`: exact compiler-input SHA-256 for the loading-screen capability
domain is not yet original-execution qualified. See
`analysis/gfx-shader/v0/buildglsl-model.md` and
`capability-domain.json` (`pending_buildglsl_closure` placeholder — not
`statically_determined`).
