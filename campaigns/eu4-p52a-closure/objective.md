# P5.2a closure — original-grounded Simple compiler-input contract

Pin: `#166` head `69a44b06ff0605323cc0295a307620ffb5178090`.
Baseline research commit (`repo_baseline_commit`): `173321b6d3258576060e7941e9bd91f608e8f543`.
EU4 executable (`eu4_executable_sha256`): `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

Load the `opengl-metal-reconstruction` skill. Do **not** edit production Metal shader
implementation in this campaign.

## Goals

1. Close Simple compiler-input identity with **exact** SHA-256 hashes per supported
   capability configuration (no open equivalence class).
2. Write `analysis/gfx-shader/v0/capability-domain.json` with a nonempty
   `supported_configurations` list that includes the intended Simple loading-screen
   `target_environment`.
3. Keep proprietary `simple.shader` / original-derived GLSL in the local fixture store
   (`.local/fixtures/…`); record fingerprints only in-repo.
4. Preserve / cite `analysis/qualification/v0/primitive-table.evidence.json`
   (`instruction_static`, engine enum 4 → GL 5).
5. Attempt bounded BuildGLSL original-code escalation; if isolation fails, emit
   `BLOCKER:` with the exact unproven dependency—do not invent hashes.
6. Raise `evidence_level` to `statically_determined` only when every supported
   configuration selects a unique qualified hash.

Write progress under `analysis/automation/eu4-p52a-closure/`.
