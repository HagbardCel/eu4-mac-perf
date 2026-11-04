# Read-only research workbench smoke

Usability record for `python3 -m reverse_engineering.research` and one OpenCode session. This is not a campaign checker and not a blinded reconstruction proof. The semantic contract in `analysis/metal-init/index-buffers/note.md` was not given to the agent.

Disposable worktree `/tmp/eu4-research-smoke`, detached at `8e649cdc96307d0594ea4436940b20613dd930e1`. `EU4_RE_EXPORT` pointed at the existing `.local/eu4/b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` root. The corpus was not copied. Before any command, `git status --short` was empty and `git diff HEAD` hashed to `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. The same empty status and the same hash were recorded after the CLI commands and again after the agent session.

## CLI

All four commands exited 0 and returned `eu4.research.v1` with `status: ok`.

- `status`: baseline `auto-v1-20261013`, pinned SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`, inventory bundle `1eaff96fe79d6b142a7c158a9701e24b78d11cc84e9e2f44cb8aed75d5bf7cae`, 4450 selected functions, index present, snapshot valid, Ghidra metadata present.
- `resolve _GfxCreateIndexBuffer`: exact, in the snapshot, entry `0x00000000015ec154`.
- `function 0x15ec154 --view static`: batch `gfx-batch-00044`, static and decompile both `success`, 43 instructions, not truncated at the default budget. The prefix is `55 48 89 e5 41 57`. The body increments the dword at `0x2238584`, reads and writes `0x2238588`, and takes the address of `0x2238590`.
- `xrefs 0x15ec154`: 11 `direct_internal` incoming edges, including caller `0x14bf9e1` at callsite `0x14bfa5b`. One outgoing thunk, `operator.new`, at callsite `0x15ec18e`. The callsite census adds that direct call and three unresolved `CALLIND` rows at `0x15ec1a9`, `0x15ec1ba`, and `0x15ec1d6`.

## OpenCode session

One session through the existing `automation.opencode.OpenCodeAgent` against local OpenCode 2.2.5, model `opencode/big-pickle`, workspace set to the disposable worktree. Session `ses_e5a01c011ffe9xifm31f4QI3Tv`, submission `msg_9ef6187d51c84cb4a953ddc60fd67038`, settled with no tool error. A prompt posted earlier with an id that did not start with `msg_` was rejected by the server before any tool ran; the runner's own ids already use that prefix. The agent was asked to investigate `_GfxSetIndexBuffer` from machine evidence with the research CLI, without modifying the repo or launching EU4 or Ghidra.

Commands it actually ran:

1. `ls -la && git status --short | head -20`
2. `ls` of the `EU4_RE_EXPORT` root
3. `ls reverse_engineering` and the first 80 lines of `research.py`
4. `cat README.md`
5. `grep` of the CLI parsers
6. the first 80 lines of `reverse_engineering/README.md`
7. `research status`
8. `research resolve _GfxSetIndexBuffer`
9. `research function 0x00000000015ec28e --view all`
10. `research xrefs 0x00000000015ec28e`
11. `sed` of `cmd_resolve`
12. `research resolve 0x0000000002253610`
13. `git status --short`

Research commands 7–10 and 12 all exited through the CLI. Command 12 returned `not_found`.

Evidence retrieved for `_GfxSetIndexBuffer`: exact entry `0x00000000015ec28e`, in snapshot, batch `gfx-batch-00044`, static and decompile `success`, 18 instructions. The stored prefix is `48 85 f6 74 28`. A null `RSI` jumps to the final `RET` at `0x15ec2bb` and skips the write to `[R14+0x68]`. The non-null path loads `R14` from `[RDI]`, reads the dword at `[RSI+4]`, calls indirectly through the qword at `0x2253610` with `EDI = 0x8893`, then stores the dword at `[RBX]` to `[R14+0x68]`. The stored decompiler text is `void _GfxSetIndexBuffer(long *param_1, undefined4 *param_2)` and names the indirect target `___glewBindBufferARB`. Xrefs: 15 `direct_internal` incoming rows from 13 callers, no outgoing call-edge, and one unresolved `CALLIND` callsite at `0x15ec2af`.

Observations that match the reviewed index-buffer contract:

- Entry, five-byte prefix, and final `RET` match the sealed body `0x15ec28e`–`0x15ec2bc`.
- Null `RSI` returns and does not write `+0x68`.
- `RDI` is dereferenced once to the master pointer, and the following store is a dword at master `+0x68`.
- The call immediate is `0x8893`, and the dword read used as the second argument is header `+4`.
- The dword written to `+0x68` is header `+0`, read after the call.
- The bind target is an unresolved indirect slot, not a resolved call edge.

Unsupported assertions in the agent's report:

- It called header `+0` an id and header `+4` a buffer handle. The instructions show two distinct dword reads. The reviewed contract identifies `+0` as the count and `+4` as the GL element-array name.
- It called `RDI` a device-context pointer. The instruction loads a qword and later uses it as the master base.
- It said a null `param_1` faults. The body does dereference `RDI` with no test; this session recorded no crash.
- It said callers cannot rely on `EAX`. The decompiler text says `void`, and `EAX` still holds the stored dword at the `RET`. No caller was inspected.
- It said 12 callers. The xrefs it retrieved are 15 rows from 13 callers, and the caller list it printed is those 13.
- It described the indirect bind as an outgoing edge. `outgoing` was empty. The unresolved call is only the callsite row.

Evidence it asked for and could not get: `resolve 0x0000000002253610` is `not_found`, so the index does not name the bind slot. It did not decompile any caller, and it said so. It did not ask for another page; `--view all` fit in the default budget.

No research command failed. The agent did not edit the worktree, copy the corpus, or launch EU4 or Ghidra. Before and after, the worktree status was empty and the diff hash was `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
