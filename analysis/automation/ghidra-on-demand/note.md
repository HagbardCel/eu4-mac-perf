# On-demand Ghidra research

Selection record for a read-only query of the existing analyzed EU4 program. This is not a campaign checker. The index-buffer contract was not given to an agent.

## Route

`mrphrazer/ghidra-headless-mcp` publishes one catalog of 212 tools. Read-only is a session default that same catalog can turn off (`program.mode.set`). The catalog also includes `program.save`, `program.image_base.set`, `patch.*`, `transaction.*`, and `ghidra.eval` / `ghidra.call` / `ghidra.script`. There is no server-side allowlist. It was not installed, and the EU4 project was not opened with it.

The agent interface is `python3 -m reverse_engineering.ghidra_query` with two commands, `function` and `address`. Each command runs one `analyzeHeadless` process with `-process -noanalysis -readOnly` and `QueryProgram.java`, then the process exits. The project directory, project name, and program name come from `ghidra_project.json`. `EU4_RE_EXPORT` only selects the corpus root that holds that file.

`-readOnly` discards in-process program changes. It is not a filesystem sandbox. OpenCode shell permissions are not one either. This session is supervised.

## Feasibility

Recorded project: `/Users/fabian/GhidraEU4/b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d/ghidra-project`, project `EU4_b3d38876`, program `eu4`. It already existed. Nothing was imported.

Toolchain: Ghidra 12.2, OpenJDK 25.0.5, `arm64`. Headless heap for these measurements was 4G.

One `-process eu4 -noanalysis -readOnly` invocation ran `ExportRendererStatic.java` and `ExportDecompilation.java` for RVA `0x15ec28e` only.

| Measurement | Value |
| --- | --- |
| Cold wall time | 8.721 s |
| `decompilation_seconds` | 0.00014 s |
| Initialization estimate | 8.721 s |
| Second invocation wall time | 7.277 s |

Identity matched: executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`, image base `4294967296`, program `eu4`. Static export status `success`, 18 instructions, name `_GfxSetIndexBuffer`.

Decompilation status was `failed`. Ghidra logged `os/mac_arm_64/decompile does not exist`. The Homebrew 12.2 formula removes native directories whose names are not `mac-arm_64`, and this build's `Decompiler/os` directory is empty. Later `ghidra_query function` calls report `decompile_status: failed` with `decompilation failed`. Instructions, references, defined data, and P-code do not use that binary.

A second open rewrote the mtimes of `~index.dat` and `~index.bak` under the project and left their bytes unchanged. No other project file changed. The live test allows that timestamp touch and fails on any content change.

Startup of about 8 seconds is slow for an interactive loop and usable for a supervised investigation. This PR does not add a persistent Ghidra process.

## Known control

`research resolve 0x2253610` is `not_found`. The sealed callsite row for `0x15ec2af` is an unresolved `CALLIND` with an empty outgoing edge. The sealed static row already stores that instruction's mnemonic, operand text, `pcode_kind: CALLIND`, and a `references_from` READ of `0x2253610`. Repeating those fields is not the new result.

`ghidra_query function 0x15ec28e` and `function _GfxSetIndexBuffer` both return the present function, 18 instructions, and 13 callers. Decompilation failed as above. The listing text at `0x15ec2af` is `CALL qword ptr [RAX]`. The instruction at `0x15ec2a0` is `LEA RAX,[0x102253610]`.

`address 0x15ec2af` is a present, initialized instruction. It has no references to that address. Its P-code mnemonics are `LOAD`, `COPY`, `INT_SUB`, `STORE`, `CALLIND`. That operation list is not in the sealed call-class field.

`address 0x2253610` is present and defined as `undefined8` with symbol `___glewBindBufferARB`. `initialized` is false, so `value_kind` is `unavailable`. There is no runtime pointer. Seventeen data references and no code references include a `WRITE` from `_glewContextInit` at `0x15d8edd` and `DATA`/`READ` pairs from `_GfxSetIndexBuffer`. The static image does not contain the function pointer EU4 stores there after GLEW initialization. The symbol is Ghidra's name for the slot, not an observed runtime target.

## What the sealed corpus already has

`research resolve CGraphics::BeginScene` finds entry `0x149d202` inside the renderer snapshot, mangled name `BeginScene`. `research resolve __ZN9CGraphics10BeginSceneEb` is `not_found`. `ghidra_query function CGraphics::BeginScene` returns that same entry, 26 instructions, 6 callers, and 3 callees (`_GfxClear`, `_GfxGetImmediateContext`, `_GfxBeginScene`), with decompilation failed. Those three callees are also in the snapshot. A stack that names `BeginScene` is not evidence that the Metal backend caused a crash.
