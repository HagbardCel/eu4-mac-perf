# `CPdxMapBorderLayer::DrawBorders` inner loop RE

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)

## Draw sites

| Site | Preferred address | Image offset | Branch |
|------|-------------------|--------------|--------|
| 0 | `0x1010cc2e8` | `0x10cc2e8` | `0x24(%r12)==0` (mode 0) |
| 1 | `0x1010cc352` | `0x10cc352` | `0x24(%r12)==1` |
| 2 | `0x1010cc3b8` | `0x10cc3b8` | other modes |

All three `callq _GfxDrawIndexed` with `ecx` either 0 or record `+0x10`.

## Record walk (28-byte stride)

Record base pointer: `%r14` (per outer batch). Index: `%r15` / `-0x80(%rbp)` with byte offset `rbx = 28 * index`.

Inner per-record loop head: `0x1010cbe55` (`testb %r13b, 0x1(%rcx)`). Skip record → `0x1010cc3bd` when flag clear.

Loop advance (`0x1010cc3bd`):

- Updates index table pointer `-0x70(%rbp)` by +4, compares against end `-0x198(%rbp)`.
- Outer counter `-0xe4(%rbp)` vs `0x10(%rbp)` at `0x1010cc3e2`.

## `943 × 31` recurrence

- Intrusive draw screen: **48,418** border same-state **adjacent pairs** over **31** frames → **~1,562 pairs/frame**.
- **943** is the dominant **inner-run length** (records per inner walk) on the paused Venice fixture — one long mode-0-style run per frame before state branches, not a compile-time constant in the binary.
- Termination: inner end pointer (`-0x198(%rbp)`) reached; outer loop exits when batch index `ebx` reaches `0x10(%rbp)`.
- **Start/end knowable before inner walk:** at `0x1010cbe38` when `r15>0`, end pointer stored to `-0x198(%rbp)` before entering `0x1010cbe55`.

## Per-iteration fast path (color)

When border color byte at `(%rcx)` matches cached `-0x51(%rbp)`, control jumps `0x1010cc1cd → 0x1010cc22d`, skipping `CColor` fetches and `GfxUpdateConstantBuffer` for that record.

## Three sites vs one pass

Sites are **mutually exclusive branches** per `SBorderDrawInfoSet` mode dword at `0x24(%r12)` for a given outer batch. Mode **0** carries the bulk (~2k draws/frame) suitable for multidraw gather; modes 1 and other require separate barrier audit (site 2 uses `+0x10`).
