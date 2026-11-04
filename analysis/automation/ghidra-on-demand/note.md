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

## Agent experiment

One supervised session through `automation.opencode.OpenCodeAgent` against local OpenCode 2.2.5, model `opencode/big-pickle`. Disposable worktree `/tmp/eu4-ghidra-ondemand`, detached at `9b12dde`. `EU4_RE_EXPORT` pointed at the existing corpus root that holds `ghidra_project.json`. The project directory stayed the recorded `project_dir`. The corpus was not copied. No new campaign, accept script, or semantic dossier was added, and the prompt did not attach `analysis/metal-init/index-buffers/note.md`.

Session `ses_e58b2f6aeffePs9vfmaqTsTnh0`, submission `msg_68b1fcd0abba486a85596c7d58e0fe07`, settled. The agent was not asked for permission (`needs_input` did not occur). Sixty-three shell commands completed. Twenty-seven of them called `research`. Seven called `ghidra_query`. The agent also read reconstruction notes and a crash report that were already in the tree.

Prompt, verbatim:

> Investigate the original implementation of `CGraphics::BeginScene(bool)` using the available research tools. Recover its control flow, relevant object accesses and graphics dependencies. Distinguish direct machine-code observations from inferred semantics. Follow references as needed. Cite original instruction addresses for significant claims. Report unresolved questions. Do not modify the repository, execute EU4 or implement replacement code.

OpenCode shell permissions are not an OS sandbox, and `-readOnly` is not one either. The session was supervised. No EU4 process was launched and no graphics code was changed. Before the session and after it, `git status --short` was empty and `git diff HEAD` hashed to `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

`ghidra_query` wall times, including the agent's JSON post-processing:

| Shell | What it asked | Wall |
| --- | --- | --- |
| Two `address` queries, `0x2251898` and `0x225189c` | defined data and references-to | 61.9 s |
| Two `function` queries, then a broken consumer | `0xf90fd2`, `0x1059b8a` | 16.0 s |
| Same two functions, output saved | listings used below | 16.3 s |
| `address 0x2253678` | GLEW slot | 7.7 s |
| `address 0x149d202` | references to `BeginScene` | 7.6 s |
| `address 0x2549b23` | the data reference target | 7.5 s |
| `address 0x2251898` again | the same counter | 7.7 s |

No query returned `response_budget`, `identity_mismatch`, or `unavailable`. The first function pair exited 1 because the agent's own Python consumer read `sys.argv` wrong; the retry wrote both responses. `function 0x1059b8a` returned 128 of 503 instructions, the script window, and the agent did not pass `--instruction-offset`. The call it cited was inside that window. Every `function` query still reports `decompile_status: failed` because `os/mac_arm_64/decompile` is missing. The pseudocode in the report for `BeginScene` is the sealed snapshot, where that function's decompilation status is `success`.

The sealed corpus already contains `BeginScene` and its three direct callees. Restating that body would not count. These `ghidra_query` results are outside that corpus:

- `research function 0xf90fd2` and `research function 0x1059b8a` are `not_in_snapshot` with null evidence. `ghidra_query function` returned both as present `Render` bodies. The saved listings contain `0xf91040 XOR ESI,ESI` / `0xf91042 CALL 0x10149d202` and `0x1059c32 XOR ESI,ESI` / `0x1059c34 CALL 0x10149d202`.
- `address 0x2251898` and `address 0x225189c` are present, defined `undefined4`, `initialized` false, `value_kind` `unavailable`, symbols `_nRenderedObjectsDebugInfo` and `_nRendered2dObjectsDebugInfo`. They have six and seven data references and no code references, including the writes at `0x149d211` and `0x149d217`. `research` cannot resolve those addresses.
- `address 0x2253678` is present, defined `undefined8`, symbol `___glewDisableVertexAttribArrayARB`, `initialized` false, `value_kind` `unavailable`. References include a `WRITE` from `_glewContextInit` at `0x15d907f` and a `READ` from `_GfxBeginScene` at `0x15ea71f`. That is the slot name and the static image, not the pointer after GLEW initialization.
- `address 0x2549b23` is present, defined `uleb128`, `initialized` true, `value_kind` `static_image`, bytes `800350da052a6cb00312f8015e8401eb`. The data reference from that address to `0x149d202` is `unresolved`. The report treats it as unwind data, not a function pointer.

The report does not claim the Metal backend caused the crash. It says a frame that returns to `0x149d229` shows the call was reached and does not locate the fault.

This experiment succeeds. The agent used `ghidra_query` for functions the snapshot does not store and for defined data whose static bytes are distinct from an unknown runtime pointer. One-shot latency stayed in the same range as the feasibility probe, so this PR still does not add a persistent process. Stronger isolation waits until unattended investigation is authorized.
