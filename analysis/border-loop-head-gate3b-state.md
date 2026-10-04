# Gate 3b — continuation state (prefix-batch model)

## Do not assume `0x1010cc3bd` as resume after N records

`0x1010cc3bd` is the **one-record loop tail**:

```asm
1010cc3bd: movq -0x70(%rbp), %rcx
1010cc3c1: leaq -0x2(%rcx), %rax
1010cc3c5: addq $0x4, %rcx
1010cc3c9: addq $0x4, %rax
1010cc3cd: cmpq -0x198(%rbp), %rax
1010cc3da: jne 0x1010cbe55
```

Jumping here after synthetically consuming **k** records risks **double advancement** unless induction is proven.

## Preferred continuation shape

```text
At hook (record 0 of prefix):
  scan eligible prefix length k
  if k < 2: fall through original loop head
  multidraw records 0..k-1
  synthesize CPU + engine state as if k records completed setup/draw OR fast-path skips per policy
  resume BEFORE record k setup — without re-entering patched detour
```

**Candidate resume (to prove):** address where record **k** would begin normal processing — e.g. after index cursor reflects **k** consumed steps, entering at `0x1010cbe55` **must not** re-trigger classifier (non-recursive trampoline / displaced `testb` path).

## CPU state table (per consumed prefix step)

Document updates for:

| State | After skip-only step | After mode-0 fast-color draw | After VBO change |
|-------|----------------------|------------------------------|------------------|
| `-0x70(%rbp)` cursor | `+4` per tail @ `0x1010cc3c5` | same | same |
| `-0x80(%rbp)` record index | from `movzwl -0x2(%rcx)` | updated at `0x1010cbe63` chain | same |
| `-0x51(%rbp)` color cache | unchanged on skip | updated on slow color @ `0x1010cc22a` | — |
| `-0x64(%rbp)` VBO cache | — | unchanged if `je 0x1010cc2c1` | updated @ `0x1010cc2be` |
| `%r14` record base | restored @ `0x1010cc1c4` | from batch | same |
| `%ebx` outer counter | only at outer step `0x1010cc3e0` | unchanged within inner | same |

**RFLAGS:** `testb` at detour sets flags; continuation must specify flag state or prove dead.

**XMM:** slow-color path uses `movaps`/`movss` @ `0x1010cc1ef`–`0x1010cc222`; fast-path join `0x1010cc22d` — prove live XMM set at continuation or spill/sync.

**Callee-saved:** trampoline must preserve ABI across detour.

## Engine / GL post-prefix invariants (batchable subset)

For a homogeneous mode-0 fast-color prefix, expect unchanged across batch:

```text
bound VBO (cached -0x64 matches all records)
bound IBO (slot from 0x60(%r12)[index])
color/object constants (-0x51, GfxUpdateConstantBuffer path skipped)
shader/effect / textures (outer batch)
```

Draw submission replaced by multidraw; **IBO bind per iteration in original loop** implies entry `ibo_matches` must be true at hook (see classifier).

## Open items

- Exact **first-unconsumed** instruction address after k-step induction (proof required).
- Non-recursive **displaced-instruction** trampoline at `0x1010cbe55`.
