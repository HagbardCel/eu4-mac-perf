# P5.2b — Shader lifecycle + engine-path disposition

Pin `campaign.json` workspace.commit to the **merged P5.2a closure** SHA before running.

## Required

1. Exact SHA-256 source admission via `eu4_gfx_shader_register_qualified_sha256` /
   `qualified-sources.json`. Transitional markers are
   `offline_lifecycle_qualification_only` — they do **not** complete the
   engine-path migration gate.
2. Evidence-derived lifecycle Obs: rejected stage replacement invalidates link;
   cover current-pointer (`master+0x70`) Obs; destroy-while-referred leaves a
   detectable stale current unless cleared by the caller contract you prove.
3. Disposition `_GfxBuildAndAddVertexShader`, `_GfxBuildAndAddPixelShader`, and
   `BuildGLSL` for Simple: ABI-qualified Metal replacements **or** originals with
   a proven GL-free reachable dependency set. Consume real source-generation
   arguments — no cached-string injection as the production strategy.
4. Call-chain closure (`automation/call_chain.py` / frame-model edges): no
   reachable original GL on the supported path, or label the result
   `offline_lifecycle_qualification_only` (intermediate only — **not** P5.2b
   completion).

Do not launch EU4. Do not invent GL names in +0/+4/+8.
