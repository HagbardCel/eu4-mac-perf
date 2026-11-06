# P5.2b — Shader lifecycle (implemented on branch metal/p52b-shader-lifecycle)

Implement Create/AddVertex/AddPixel/Link/Set/Destroy for the P5.2a Simple
contract. Backend registry keyed by lifetime id. Success only after real Metal
stage construction. Do not launch EU4. Do not invent GL names in +0/+4/+8.

Pin `campaign.json` workspace.commit to the merged P5.2a SHA before running.
Essential prerequisite set is bounded (Simple + draw resources); do not expand
into a general shader compiler.

Editable paths: `benchmark/eu4_gfx_shader.*`, `benchmark/eu4_gfx_entries.def`,
`benchmark/eu4_gfx_entry_patch.c`, shader tests, analysis notes under
`analysis/automation/eu4-p52b-shader-lifecycle/`.
