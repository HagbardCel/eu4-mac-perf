# Gate 3b — continuation state (prefix-batch model)

**Criterion 6 proof:** CFG forward-use audit on pinned GOG 1.37.5 disassembly — [drawborders-inner-walk-disasm.txt](evidence/drawborders-inner-walk-disasm.txt) (`otool -tv`, sha256 `b3d38876…`). Machine conclusions: [border-hook-site.json](border-hook-site.json) `continuation_liveness_proof`.

**Criterion 8:** resume topology only (interior vs `0x1010cc3e0`, non-recursive re-entry). Register/stack contract is criterion **6**.

## Criterion 6 formulation

Post-state equivalence on **all reachable successor paths** from each resume IP:

| Part | Audit |
|------|--------|
| **A** | Caller-saved GPR, XMM0–15, **RFLAGS** after `GfxSetIndexBuffer` + `glMultiDrawElements` |
| **B** | Callee-saved `%rbp`, `%rbx`, `%r12`–`%r15` + stack locals skipped by batching |

Calls are **uses** of ABI argument registers. Caller-saved registers not live-in to a call are killed by the call. Width-sensitive defs (`movl`/`movw`/`movb`, XMM scalars). **No YMM/AVX** in this address range (`drawborders-inner-walk-disasm.txt` — `movss`/`movaps` only).

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
```

## Terminal trampoline (mandatory)

Direct `jmp 0x1010cc3e0` skips `movl -0xe4(%rbp), %ebx` @ `0x1010cc3d4`. `%rbx` may hold stride offset on mode-0 path (`0x1010cc285`–`0x1010cc289`).

```text
movl -0xe4(%rbp), %ebx
jmp  0x1010cc3e0
```

## Interior — displaced hook (`0x1010cbe55`)

| State | Status | Evidence |
|-------|--------|----------|
| `%rcx` | LIVE — synthesize first-unconsumed cursor | `testb %r13b,0x1(%rcx)` @ `0x1010cbe55` |
| `-0x70` | LIVE — displaced `movq %rcx,-0x70` @ `0x1010cbe59` | |
| RFLAGS | LIVE — `testb` + `je` @ `0x1010cbe5d` | trampoline reproduces |

## Interior — draw (`0x1010cbe63` → mode-0 @ `0x1010cc2e8`, fast-color via `0x1010cc1cd`→`0x1010cc22d`)

**Scope:** eligible homogeneous prefix (classifier); not slow-color `0x1010cc1cf` XMM block.

### A — caller-saved (incoming post-MDE value)

| Reg | Status | First read / define on path | Notes |
|-----|--------|----------------------------|--------|
| `%rcx` | LIVE | read `movzwl -0x2(%rcx)` @ `0x1010cbe63` | synthesize cursor |
| `%rax` | DEAD | define `movq 0x60(%r12),%rax` @ `0x1010cc2c1` before GfxSetIndexBuffer arg setup | |
| `%rdi` | DEAD | define `movq -0x60(%rbp),%rdi` @ `0x1010cc2ca` / `0x1010cc2e2` | call-use to helpers |
| `%rsi` | DEAD | define `movq (%rax,%r15,8),%rsi` @ `0x1010cc2c6` | then GfxSetIndexBuffer **use** |
| `%rdx` | DEAD | define `movzwl 0x18(%r14,%rbx)` @ `0x1010cc28c` (cmp only) / `movzwl +0x04` @ `0x1010cc2dc` for GfxDrawIndexed | |
| `%r8`–`%r11` | DEAD | no read `0x1010cbe63`…`0x1010cc2e8` on mode-0 fast join | |
| XMM0–15 | DEAD | no XMM **read** on `0x1010cc22d`…`0x1010cc2e8` (slow XMM @ `0x1010cc200`+ bypassed) | batch calls may clobber |
| RFLAGS | DEAD | flag consumers on fast path use fresh `cmp`/`test` | |

GfxDrawIndexed @ `0x1010cc2e8`: `%rdi`,`%rsi`,`%edx`,`%ecx` are **call arguments** (defined immediately before) — resume-era values not read.

### B — callee-saved / locals

| State | Status | Evidence |
|-------|--------|----------|
| `%r12` | preserve hook-time | `SBorderDrawInfoSet*`; callees preserve; batch does not change expected value |
| `%r14` | redefine before use | `movq 0x78(%r12),%r14` @ `0x1010cbe67` |
| `%r15` | redefine | `movq -0x80(%rbp),%r15` @ `0x1010cc27d` |
| `%rbx` | redefine | `leaq` chain @ `0x1010cc281`–`0x1010cc289` |
| `-0x80` | read then refresh | set @ `0x1010cbe6f`; consumed @ `0x1010cc27d` |
| `-0x51`, `-0x64` | unchanged | homogeneous batch; no terminal fixup on interior |
| `%ebp` frame | preserve | standard frame |

## Interior — skip tail (`0x1010cc3bd`)

| State | Status | Evidence |
|-------|--------|----------|
| `%rcx` | define from stack | `movq -0x70(%rbp),%rcx` @ `0x1010cc3bd` — not resume `%rcx` |
| `%rax` | define | `leaq -0x2(%rcx),%rax` @ `0x1010cc3c1` before `cmp` @ `0x1010cc3cd` |
| Other caller-saved / XMM | DEAD | no read before `jne 0x1010cbe55` @ `0x1010cc3da` or merge |
| RFLAGS | LIVE through `cmp`/`jne` | then dead at loop head if `testb` redefines |

## Terminal (`0x1010cc3e0` successors)

Trampoline supplies `%ebx` from `-0xe4` before `incl`.

| State | Status | Forward successors `0x1010cc3e0`…`0x1010cc3fb` |
|-------|--------|--------------------------------------------------|
| `%ebx` | LIVE — **synthesize** from `-0xe4(%rbp)` | `incl %ebx` @ `0x1010cc3e0` |
| `%rax`,`%rcx` | DEAD | no read before `0x1010cc3eb` |
| `%rdi`,`%rsi` | DEAD until define @ `0x1010cc3f7`/`0x1010cc3eb` | profile tail |
| `-0x70(%rbp)` | DEAD | no read in terminal span; next read @ `0x1010cbe38` only on **future** inner walk |
| `-0x198` | unchanged | not read on terminal span |
| XMM / RFLAGS | DEAD before `incl` | `cmp`/`jne` not taken; no flag consumer before `incl` |

## glMultiDrawElements (mutation)

```text
rdi=GL_TRIANGLES rsi=&count rdx=type rcx=&indices r8=drawcount
```

## Non-recursive re-entry

14-byte patch @ `0x1010cbe55`; [border-hook-site.json](border-hook-site.json).
