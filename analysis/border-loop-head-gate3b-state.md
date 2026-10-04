# Gate 3b — continuation state (prefix-batch model)

## Do not assume `0x1010cc3bd` as resume after k drawable records

`0x1010cc3bd` is the **one-record loop tail** (advances live `%rcx`, compares to `-0x198(%rbp)`):

```asm
1010cc3bd  movq -0x70(%rbp), %rcx
1010cc3c1  leaq -0x2(%rcx), %rax
1010cc3c5  addq $4, %rcx
1010cc3c9  addq $4, %rax
1010cc3cd  cmpq -0x198(%rbp), %rax
1010cc3da  jne 0x1010cbe55
1010cc3e0  incl outer ebx …
```

`-0x70(%rbp)` is **written at the next loop head** (`0x1010cbe59`), not incremented in the tail. The tail mutates **live `%rcx`** then stores on re-entry.

## Continuation classes

| Class | When | Action |
|-------|------|--------|
| **Interior** | `termination_kind == BARRIER` or `SCAN_CAP` | `%rcx` = first-unconsumed index entry; displaced `testb` + `movq`; branch via original `je` semantics @ `0x1010cbe5d`; must not re-enter classifier detour |
| **Terminal** | `termination_kind == WALK_END` | No phantom record test; synthesize state after last consumed step; resume **outer-step** @ `0x1010cc3e0` |

Classifier sets `WALK_END` only when `walk_exhausted=True` (induction reached `-0x198` bound), **not** when `len(side_entries)` equals scan cap.

## CPU live state (interior trampoline)

| State | Requirement |
|-------|-------------|
| `%rcx` | Points at first-unconsumed associated entry |
| `-0x70(%rbp)` | Updated by displaced `movq %rcx, -0x70` |
| RFLAGS | From displaced `testb` before `je` |
| `-0x80(%rbp)` record index | Not loaded until fall-through past `0x1010cbe63` — **dead** at trampoline entry |
| XMM | Prove dead at fast-color join or spill in mutation PR |

## CPU live state (terminal @ `0x1010cc3e0`)

| State | After homogeneous mode-0 prefix |
|-------|----------------------------------|
| `%rcx` / `%rax` | As if tail completed for all consumed index steps (cursor at end bound) |
| `-0x70(%rbp)` | Final cursor value for last consumed entry |
| `-0xe4(%rbp)` / `%ebx` | Outer batch index — reloaded @ `0x1010cc3d4` before `0x1010cc3e0` in original flow |
| `-0x51`, `-0x64` | Cached color/VBO match prefix batch key |
| IBO shadow (`ONE_BIND`) | Established by batch `GfxSetIndexBuffer` before MDEBV |

RFLAGS at `0x1010cc3e0`: **dead** (no flag consumer before outer `incl`).

## Engine / GL (`ONE_BIND`)

Homogeneous prefix: one `GfxSetIndexBuffer(prefix ibo_argument)`, MDEBV, VBO/color caches unchanged across batch.

## Non-recursive re-entry

Patch @ `0x1010cbe55` uses 14-byte `RIP_INDIRECT_ABSOLUTE_JMP` to trampoline. Resume into original code uses displaced-instruction stub or TLS one-shot — see [border-hook-site.json](border-hook-site.json).
