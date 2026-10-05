# Gate 3b — continuation state (prefix-batch model)

**Criterion 6 proof:** CFG forward-use audit on pinned GOG 1.37.5 **full `DrawBorders`** disassembly — [drawborders-drawborders-fn-disasm.txt](evidence/drawborders-drawborders-fn-disasm.txt) (`border_draw_inner_disasm.sh`, sha256 `b3d38876…`). Machine conclusions: [border-hook-site.json](border-hook-site.json) `continuation_liveness_proof`.

**Criterion 8:** resume topology only (interior vs `0x1010cc3e0`, non-recursive re-entry). Register/stack contract is criterion **6**.

## Criterion 6 formulation

Post-state equivalence on **all reachable successor paths** from each resume IP:

| Part | Audit |
|------|--------|
| **A** | Caller-saved GPR, XMM0–15, **RFLAGS** after `GfxSetIndexBuffer` + `glMultiDrawElements` |
| **B** | Callee-saved `%rbp`, `%rbx`, `%r12`–`%r15` + stack locals skipped by batching |

Calls are **uses** of ABI argument registers. Caller-saved registers not live-in to a call are killed by the call. Width-sensitive defs (`movl`/`movw`/`movb`, XMM scalars). **No YMM/AVX** in `DrawBorders` (`movss`/`movaps` only).

## v1 batching scope (classifier-aligned)

- **Hook gate:** `uint32(-0xe4(%rbp))` not in special precolor set `{4,5,7}` (mask `0xB0`).
- **Batched interior:** homogeneous mode-0 prefix only; batch may end with `termination_kind` `BARRIER`, `SCAN_CAP`, or `WALK_END` while `batch_eligible` (prefix length ≥ 2).
- **Post-MDE resume:** when `termination_kind != WALK_END`, trampoline resumes at the **first unconsumed** record (not necessarily the simple `je 0x1010cc26b` path). Criterion 6 requires proofs for **all** such interior resumes, not only the homogeneous draw slice.

### Interior resume contract (`%rbx` / `%ebx`)

`%rbx` and `%ebx` are the **same physical register**. After MDE, the trampoline must not assume a preserved stride value:

```text
movl -0xe4(%rbp), %ebx          # outer_batch_index (read @ 0x1010cbe6c before stride setup)
movq <first_unconsumed>, %rcx
# displaced testb %r13b,0x1(%rcx); movq %rcx,-0x70; je …
```

Stride is redefined later (`leaq` @ `0x1010cc281`–`0x1010cc289`) only after outer-index checks @ `0x1010cbe6c`–`0x1010cbe81`.

## Interior — first-unconsumed boundary matrix (post-MDE)

Audited on [drawborders-drawborders-fn-disasm.txt](evidence/drawborders-drawborders-fn-disasm.txt). **A:** caller-saved/XMM/RFLAGS at helper entry; **B:** callee-saved preserved (SysV + batch callees).

| Boundary (classifier) | Resume / entry | Required conclusion | Disasm anchors |
|------------------------|----------------|---------------------|----------------|
| **Color transition** | `0x1010cc1cf` → mismatch → `0x1010cc1de`…`0x1010cc22d` | **A:** `%rdi`,`%rsi`,`%rdx`,`%ecx`,`%r8d`,`%r9d` defined @ `0x1010cc22d`–`0x1010cc260` before `callq` @ `0x1010cc242`/`0x1010cc266`; XMM filled on stack before calls. **B:** `%rbp`,`%r12` preserved; `%r14` still valid from `movq 0x78(%r12),%r14` @ `0x1010cbe67` (eligible non-special path — not the `{4,5,7}` `-0x190` restore @ `0x1010cc1c4`) | `0x1010cc1de`–`0x1010cc266` |
| **VBO transition** | mode-0 @ `0x1010cc28c` → `jne` → `0x1010cc297` | **A:** `%rdx` from `movzwl 0x18(%r14,%rbx)`; `GfxSetVertexBuffers` args @ `0x1010cc297`–`0x1010cc2b1` (`%rdi`,`%esi`,`%ecx`,`%r8d`,`%r9d` xor/define). **B:** `%rbx` stride live for addressing; outer `%ebx` already synthesized from `-0xe4` at interior re-entry | `0x1010cc297`–`0x1010cc2b1` |
| **IBO transition** | mode-0 GfxSetIndexBuffer path | **A:** `%rax`,`%rsi`,`%rdi` defined @ `0x1010cc2c1`–`0x1010cc2ce` before `callq 0x1015ec28e`. **B:** same as simple mode-0 tail | `0x1010cc2c1`–`0x1010cc2ce` |
| **Null IBO** (fall-through; not batched) | `GfxSetIndexBuffer` with `rsi==0` | Helper no-op @ `0x1015ec291`; DrawBorders still defines args before draw — same mode-0 reconstruction as IBO path | helper audit + `0x1010cc2c6` |
| **Zero triangles** | `GfxDrawIndexed` with `esi<=0` | **A:** `%edi`,`%esi`,`%edx`,`%ecx` defined immediately before `0x1010cc2e8` (or site-appropriate branch). **B:** preserved | `0x1010cc2d3`–`0x1010cc2e8` |
| **Scan cap** | next record still homogeneous | **Interior — simple draw** table below (`je 0x1010cc26b` path) + `%ebx` synthesis | `0x1010cc1d5`… |
| **Skip** (`SKIP` barrier) | visibility clear → `0x1010cc3bd` | **Interior — skip tail** table | `0x1010cc3bd` |
| **Simple homogeneous draw** (included in batch / scan-cap continuation) | `je 0x1010cc26b` @ `0x1010cc1d5` | **Interior — simple draw** table | `0x1010cc26b`…`0x1010cc2e8` |

**Not in v1 batch interior:** special outer `{4,5,7}` precolor (`0x1010cbe87+`) — hook gate; fast-path `jmp 0x1010cc22d` @ `0x1010cc1cd` without color mismatch — excluded from eligible batch scope.

## Loop tail

```asm
1010cc3bd  movq -0x70(%rbp), %rcx
1010cc3c1  leaq -0x2(%rcx), %rax
1010cc3c5  addq $4, %rcx
1010cc3c9  addq $4, %rax
1010cc3cd  cmpq -0x198(%rbp), %rax
1010cc3d4  movl -0xe4(%rbp), %ebx
1010cc3da  jne 0x1010cbe55
1010cc3e0  incl %ebx
1010cc3e2  cmpl 0x10(%rbp), %ebx
1010cc3e5  jne 0x1010cbc7f
1010cc3eb  movq 0x8(%r12), %rax   ; profile tail (outer loop done)
```

## Terminal trampoline (mandatory)

Direct `jmp 0x1010cc3e0` skips `movl -0xe4(%rbp), %ebx` @ `0x1010cc3d4`. Trampoline must reload outer index from `-0xe4` (not assume a preserved stride in `%rbx`).

```text
movl -0xe4(%rbp), %ebx
jmp  0x1010cc3e0
```

### Terminal CFG successors (audited on full function)

| Branch @ `0x1010cc3e5` | Target | `-0x70(%rbp)` | Notes |
|------------------------|--------|---------------|--------|
| taken (`ebx != limit`) | `0x1010cbc7f` | **Define** `movq %rax, -0x70` @ `0x1010cbca1` before next **read** @ `0x1010cbe38` | outer-batch setup; trampoline need not preserve stale `-0x70` |
| not taken | `0x1010cc3eb` | **No read** through `0x1010cc3fb` | profile `callq`; frame unwinds |

**`-0x70` fallback (if ever required):** at `WALK_END`, `last_cursor + 2 == end_bound` with `end_bound = -0x198(%rbp)` → `last_cursor = -0x198(%rbp) - 2`. Current CFG: **omit** trampoline write (both successors redefine or ignore before read). See [border-loop-head-patch-plan.md](border-loop-head-patch-plan.md).

## Interior — displaced hook (`0x1010cbe55`)

| State | Status | Evidence |
|-------|--------|----------|
| `%rcx` | LIVE — synthesize first-unconsumed cursor | `testb %r13b,0x1(%rcx)` @ `0x1010cbe55` |
| `%rbx` / `%ebx` | LIVE — **synthesize** outer index | `movl -0xe4(%rbp),%ebx` before resume; first read `cmpl $0x7,%ebx` @ `0x1010cbe6c` |
| `-0x70` | LIVE — displaced `movq %rcx,-0x70` @ `0x1010cbe59` | |
| RFLAGS | LIVE — `testb` + `je` @ `0x1010cbe5d` | trampoline reproduces |

## Interior — simple draw (`0x1010cbe63` → `je 0x1010cc26b` @ `0x1010cc1d5` → mode-0 @ `0x1010cc2e8`)

### A — caller-saved (incoming post-MDE value)

| Reg | Status | First read / define on path | Notes |
|-----|--------|----------------------------|--------|
| `%rcx` | LIVE | read `movzwl -0x2(%rcx)` @ `0x1010cbe63` | synthesize cursor |
| `%rax` | DEAD | define `movq 0x60(%r12),%rax` @ `0x1010cc2c1` before GfxSetIndexBuffer arg setup | |
| `%rdi` | DEAD | define `movq -0x60(%rbp),%rdi` @ `0x1010cc2ca` / `0x1010cc2e2` | call-use to helpers |
| `%rsi` | DEAD | define `movq (%rax,%r15,8),%rsi` @ `0x1010cc2c6` | then GfxSetIndexBuffer **use** |
| `%rdx` | DEAD | define `movzwl 0x18(%r14,%rbx)` @ `0x1010cc28c` (cmp only) / `movzwl +0x04` @ `0x1010cc2dc` for GfxDrawIndexed | |
| `%r8`–`%r11` | DEAD | no read `0x1010cbe63`…`0x1010cc2e8` on mode-0 eligible join | |
| XMM0–15 | DEAD | no XMM **read** on eligible path before `0x1010cc2e8` | batch calls may clobber |
| RFLAGS | DEAD | flag consumers on path use fresh `cmp`/`test` | |

GfxDrawIndexed @ `0x1010cc2e8`: `%rdi`,`%rsi`,`%edx`,`%ecx` are **call arguments** (defined immediately before) — resume-era values not read.

### B — callee-saved / locals

| State | Status | Evidence |
|-------|--------|----------|
| `%rbp` | **PRESERVE_FRAME_POINTER** | standard frame; no redefine on eligible interior path before return to tail |
| `%r13` | **PRESERVE_FULL** | hook `testb %r13b,0x1(%rcx)`; special-path `movb` @ `0x1010cc1c0` bypassed on eligible path; mode-1/site paths redefine `%r13` later — not in v1 batch interior |
| `%r12` | preserve hook-time | `SBorderDrawInfoSet*`; callees preserve |
| `%r14` | redefine before use | `movq 0x78(%r12),%r14` @ `0x1010cbe67` |
| `%r15` | redefine | `movq -0x80(%rbp),%r15` @ `0x1010cc27d` |
| `%rbx` / `%ebx` | LIVE outer index then **redefine** stride | `cmpl`/`btl` @ `0x1010cbe6c`–`0x1010cbe81` use `%ebx`; `leaq` @ `0x1010cc281`–`0x1010cc289` overwrites full `%rbx` |
| `-0x80` | read then refresh | set @ `0x1010cbe6f`; consumed @ `0x1010cc27d` |
| `-0x51`, `-0x64` | unchanged | homogeneous batch; no terminal fixup on interior |

## Interior — skip tail (`0x1010cc3bd`)

| State | Status | Evidence |
|-------|--------|----------|
| `%rcx` | define from stack | `movq -0x70(%rbp),%rcx` @ `0x1010cc3bd` — not resume `%rcx` |
| `%rax` | define | `leaq -0x2(%rcx),%rax` @ `0x1010cc3c1` before `cmp` @ `0x1010cc3cd` |
| Other caller-saved / XMM | DEAD | no read before `jne 0x1010cbe55` @ `0x1010cc3da` or merge |
| RFLAGS | LIVE through `cmp`/`jne` | then dead at loop head if `testb` redefines |

## Terminal (`0x1010cc3e0` and successors)

Trampoline supplies **outer** `%ebx` from `-0xe4(%rbp)` before `incl` @ `0x1010cc3e0`. Audit spans `0x1010cc3e0` through both successors of `jne` @ `0x1010cc3e5` until the next relevant read/redefine on each path ([drawborders-drawborders-fn-disasm.txt](evidence/drawborders-drawborders-fn-disasm.txt)).

### Caller-saved (terminal span)

| State | Status | Evidence |
|-------|--------|----------|
| `%eax` / `%rax` | DEAD | redefined on taken path @ `0x1010cbc7f`; defined before use on exit @ `0x1010cc3eb` |
| `%ecx` / `%rcx` | DEAD | same |
| `%edi` / `%rdi`, `%esi` / `%rsi` | DEAD until define | exit: `0x1010cc3eb` / `0x1010cc3f7`; taken: outer-loop defs before calls |
| `%edx` / `%rdx` | DEAD | taken: defined @ `0x1010cbd42` (`movq 0x18(%r12),%rdx`) before use |
| `%r8`–`%r11` | DEAD | taken: `%r8d`/`%r9d` defined before `callq` @ `0x1010cbd1a`/`0x1010cbcbf`; `%r10`/`%r11` not read before calls/exit |
| XMM0–15 | DEAD before `incl` | no XMM read on `0x1010cc3e0`…`0x1010cc3e5` |
| RFLAGS | DEAD before `incl` | `cmp`/`jne` @ `0x1010cc3e2`–`0x1010cc3e5`; no flag consumer before `incl` |

### Callee-saved (both terminal successors)

| State | Taken `0x1010cbc7f` | Not taken `0x1010cc3eb` |
|-------|---------------------|-------------------------|
| `%rbp` | PRESERVE_FRAME_POINTER | PRESERVE_FRAME_POINTER |
| `%r12` | LIVE — **PRESERVE_HOOK_VALUE**; first read `movq 0x8(%r12),%rdi` @ `0x1010cbca5` | LIVE — **PRESERVE_HOOK_VALUE**; first read `movq 0x8(%r12),%rax` @ `0x1010cc3eb` |
| `%r13` | PRESERVE_FULL into later inner walk / next hook | PRESERVE_FULL through profile tail (callees preserve) |
| `%r14` | DEAD — redefine `movq %rax,%r14` @ `0x1010cbcc4` before use | DEAD — not read before function epilogue / outer exit |
| `%r15` | DEAD — redefine `movslq (%rcx,%rax,4),%r15` @ `0x1010cbc8b` before mode-0 use | DEAD — not read on exit span before redefine on other paths |
| `%rbx` / `%ebx` | LIVE — **synthesize** `movl -0xe4,%ebx` then `incl` @ `0x1010cc3e0`; used @ `0x1010cbc7f` (`movl %ebx,%eax`); stride role only after next inner re-entry | same synthesis before `cmp` @ `0x1010cc3e2` |

Batch + trampoline must preserve callee-saved registers per SysV; inserted GL helpers are call sites that preserve `%rbp`, `%rbx`, `%r12`–`%r15`.

### Stack locals (terminal accounting)

| Slot | Status | Evidence |
|------|--------|----------|
| `-0x70(%rbp)` | DEAD (no trampoline write) | taken: redefine `movq %rax,-0x70` @ `0x1010cbca1` before read @ `0x1010cbe38`; exit: never read |
| `-0x198(%rbp)` | DEAD after terminal | taken: redefine `movq %rax,-0x198` @ `0x1010cbe40` before tail `cmp` @ `0x1010cc3cd`; exit: not read |
| `-0xe4(%rbp)` | LIVE — synthesis source for `%ebx` | read for trampoline `movl -0xe4,%ebx`; next inner walk overwrites @ `0x1010cbe4f` |
| `-0x51(%rbp)` | PRESERVED / equivalent | homogeneous eligible batch: color unchanged vs original per-record path; next inner walk resets `movb $-1,-0x51` @ `0x1010cbe4b` |
| `-0x64(%rbp)` | PRESERVED / equivalent | homogeneous eligible batch: matching VBO ⇒ original also leaves cache unchanged; **persists across outer batches** (not reset @ `0x1010cbc7f`) |
| `-0x80(%rbp)` | DEAD after terminal | next inner walk writes `movq %rcx,-0x80` @ `0x1010cbe6f` before mode-0 read @ `0x1010cc27d` |

## glMultiDrawElements (mutation)

```text
rdi=GL_TRIANGLES rsi=&count rdx=type rcx=&indices r8=drawcount
```

## Non-recursive re-entry

14-byte patch @ `0x1010cbe55`; [border-hook-site.json](border-hook-site.json).
