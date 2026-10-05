# `CPdxMapBorderLayer::DrawBorders` inner loop RE

**Superseded in part by:** [border-loop-head-data-model.md](border-loop-head-data-model.md) (control-flow-scoped glossary, `%rcx` / `%r13b`).

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

## Per-iteration color paths (v1 batching scope)

**Special outer batch** (`uint32(-0xe4(%rbp))` in `{4,5,7}` per mask `0xB0` @ `0x1010cbe6c`–`0x1010cbe81`): enters precolor block @ `0x1010cbe87` with per-record helpers — **excluded** from v1 multidraw (classifier hook gate).

**Eligible simple path** (non-special outer batch only): slow color compare @ `0x1010cc1cf`; on match, `je 0x1010cc26b` @ `0x1010cc1d5` → mode-0 draw setup. **Not** equivalent to `0x1010cc1cd → 0x1010cc22d`, which still runs helper calls @ `0x1010cc242` / `0x1010cc266` before the mode check.

## Three sites vs one pass

Sites are **mutually exclusive branches** per `SBorderDrawInfoSet` mode dword at `0x24(%r12)` for a given outer batch. Mode **0** carries the bulk (~2k draws/frame) suitable for multidraw gather; modes 1 and other require separate barrier audit (site 2 uses `+0x10`).
