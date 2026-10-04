# Intrusive diagnostic salvage (TAIL only)

- Status: **usable_ordinal_hint**
- TAIL salvage produced ordinal hints; treat as non-quantitative
- Salvage detail rows: **709371**
- Dropped profiler records (run): **191966**

## C1 vs C2 aggregate counters

- draws: relative spread 0.0002
- state_calls: relative spread 0.0011
- uniform_calls: relative spread 0.0014
- texture_binds: relative spread 0.0008
- buffer_binds: relative spread 0.0010
- program_switches: relative spread 0.0018

## TAIL window retention

- Window **24**: median r_S=0.8225095640646984, r_U=0.7376801750811259, detail_rows=355273
- Window **25**: median r_S=0.8040093489459179, r_U=0.7332654139310241, detail_rows=354098

## Rank stability (reporting only)

- Top-16 overlap between windows [24, 25]: **1.0**
- Count-complete S overlap: **1.0**
- Spearman ρ (all frames): **1.0**
- Kendall τ (all frames): **1.0**
- Spearman ρ (count-complete S): **1.0**
- Kendall τ (count-complete S): **1.0**
- Count-complete U overlap: **1.0**
- Spearman ρ (count-complete U/u): **1.0**
- Kendall τ (count-complete U/u): **1.0**

## Count-complete S callsite drift (TAIL windows)

- #1 {'offset': 22986657, 'symbol': '_GfxSetVertexBuffers', 'demangled': '_GfxSetVertexBuffers', 'eu4': 'eu4+0x15ebfa1'}: W24=12868.0, W25=12868.0, drift=0.0
- #2 {'offset': 22981159, 'symbol': '__ZN13SShaderOpenGL24EnableVertexAttribArraysEPP19SVertexBufferOpenGLPjS3_', 'demangled': 'SShaderOpenGL::EnableVertexAttribArrays(SVertexBufferOpenGL**, unsigned int*, unsigned int*)', 'eu4': 'eu4+0x15eaa27'}: W24=12866.0, W25=12866.0, drift=0.0
- #3 {'offset': 22981191, 'symbol': '__ZN13SShaderOpenGL24EnableVertexAttribArraysEPP19SVertexBufferOpenGLPjS3_', 'demangled': 'SShaderOpenGL::EnableVertexAttribArrays(SVertexBufferOpenGL**, unsigned int*, unsigned int*)', 'eu4': 'eu4+0x15eaa47'}: W24=12866.0, W25=12866.0, drift=0.0
- #4 {'offset': 22981178, 'symbol': '__ZN13SShaderOpenGL24EnableVertexAttribArraysEPP19SVertexBufferOpenGLPjS3_', 'demangled': 'SShaderOpenGL::EnableVertexAttribArrays(SVertexBufferOpenGL**, unsigned int*, unsigned int*)', 'eu4': 'eu4+0x15eaa3a'}: W24=12866.0, W25=12866.0, drift=0.0
- #5 {'offset': 22990117, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecd25'}: W24=7809.0, W25=7809.0, drift=0.0
- #6 {'offset': 22990088, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecd08'}: W24=7809.0, W25=7809.0, drift=0.0
- #7 {'offset': 22985734, 'symbol': '_GfxSetShader', 'demangled': '_GfxSetShader', 'eu4': 'eu4+0x15ebc06'}: W24=5482.0, W25=5482.0, drift=0.0
- #8 {'offset': 22981050, 'symbol': '__ZN13SShaderOpenGL24EnableVertexAttribArraysEPP19SVertexBufferOpenGLPjS3_', 'demangled': 'SShaderOpenGL::EnableVertexAttribArrays(SVertexBufferOpenGL**, unsigned int*, unsigned int*)', 'eu4': 'eu4+0x15ea9ba'}: W24=3621.0, W25=3621.0, drift=0.0
- #9 {'offset': 22987441, 'symbol': '_GfxSetIndexBuffer', 'demangled': '_GfxSetIndexBuffer', 'eu4': 'eu4+0x15ec2b1'}: W24=2420.0, W25=2420.0, drift=0.0
- #10 {'offset': 22985829, 'symbol': '__ZN13SShaderOpenGL6SetAllEv', 'demangled': 'SShaderOpenGL::SetAll()', 'eu4': 'eu4+0x15ebc65'}: W24=1083.0, W25=1083.0, drift=0.0
- #11 {'offset': 22990258, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecdb2'}: W24=730.0, W25=730.0, drift=0.0
- #12 {'offset': 22990275, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecdc3'}: W24=730.0, W25=730.0, drift=0.0
- #13 {'offset': 22990292, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecdd4'}: W24=730.0, W25=730.0, drift=0.0
- #14 {'offset': 22990309, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecde5'}: W24=730.0, W25=730.0, drift=0.0
- #15 {'offset': 22990333, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecdfd'}: W24=730.0, W25=730.0, drift=0.0
- #16 {'offset': 22990202, 'symbol': '__ZL33InternalSetTextureAndSamplerStatejP14STextureOpenGLPK19SSamplerStateOpenGLb', 'demangled': 'InternalSetTextureAndSamplerState(unsigned int, STextureOpenGL*, SSamplerStateOpenGL const*, bool)', 'eu4': 'eu4+0x15ecd7a'}: W24=675.0, W25=675.0, drift=0.0

Ordinal callsite ranks from TAIL windows only; C1/C2 provide aggregate F-counter stability. U/u program/location fields are not trusted under flag 2048.
