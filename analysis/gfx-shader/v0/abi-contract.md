# Shader object ABI contract (P5.2a)

Object size: `0x78` bytes (`_GfxCreateShader` / `operator.new(0x78)`).

## Mapped fields (instruction / dossier evidence)

| Offset | Original representation | Evidence |
| --- | --- | --- |
| `+0` | GL program name (`GLuint`) | Create stores `glewCreateProgramObjectARB` result; Metal stub stores `0` |
| `+4` | GL vertex shader name | AddVertexShader attach/detach; header comments on stub |
| `+8` | GL pixel shader name | AddPixelShader attach/detach; header comments on stub |

Further bytes inside `0x78` are **Unresolved** offline. Do not invent Metal pointers into unmapped offsets.

## Master context

| Location | Role |
| --- | --- |
| `master+0x70` | Current shader object pointer (`_GfxSetShader`) |
| `master+0x128`, `master+0x170` | Dirty flags set when shader changes (dossier `_GfxSetShader`) |

## Logical link vs Metal pipeline

- **Logical link (P5.2b):** engine-visible success/failure after both stages exist and the stage interface is accepted. Replaces `SShaderOpenGL::LinkProgram` observability without claiming Metal runs `glLinkProgram`.
- **PSO (P5.2c):** `MTLRenderPipelineState` from stage functions + vertex descriptor + attachment formats + blend/color-write/sample state. May be constructed lazily when layout and RT state are known.

## Unresolved

- Exact layout of attribute/uniform reflection tables inside the `0x78` object or side tables used by `SetAll`.
- Byte-identical `BuildGLSL` compiler input for technique `Simple` under Metal-supported capability conditions.
