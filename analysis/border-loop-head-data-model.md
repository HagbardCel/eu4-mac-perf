# Border loop-head data model (`DrawBorders` inner walk)

Disassembly source: local `otool -tv` on GOG `eu4` (image base `0x100000000`).

## Control-flow-scoped glossary

| Range | Register / local | Meaning | Proven by |
|-------|------------------|---------|-----------|
| `0x1010cbe38` | `-0x70(%rbp)` → `%rcx` | Index-table cursor for inner walk | `movq -0x70, %rcx` @ `0x1010cbe38` |
| `0x1010cbe3c` | `%rax` | End bound: `cursor + 4*r15` stored to `-0x198(%rbp)` | `leaq (%rcx,%r15,4), %rax` |
| `0x1010cbe47` | `%rcx` | Cursor advanced `+2` before loop head | `addq $2, %rcx` |
| `0x1010cbe55` | `%rcx` | Associated entry pointer for skip test | `testb %r13b, 0x1(%rcx)` |
| `0x1010cbe59` | `-0x70(%rbp)` | Cursor saved after test | `movq %rcx, -0x70` |
| `0x1010cbe63` | `%ecx` | **Record table index** from index entry | `movzwl -0x2(%rcx), %ecx` → `-0x80(%rbp)` @ `0x1010cbe6f` |
| `0x1010cbe67` | `%r14` | **Record array base** for this batch | `movq 0x78(%r12), %r14` |
| `0x1010cc281` | `%rbx` | Byte offset `28 * record_index` into `%r14` | `leaq` chain @ `0x1010cc281`–`0x1010cc289` |
| `0x1010cc1d3` | `%cl` | **Color byte** at `(%rax)` where `%rax` = index cursor | slow-path compare vs `-0x51(%rbp)` |
| `0x1010cc26b` | `%eax` | Draw **mode** dword at `0x24(%r12)` | outer `SBorderDrawInfoSet` context |
| `0x1010cc3bd` | `%rcx`, `%rax` | **One-iteration tail**: adjust cursor, compare to `-0x198`, loop or outer step | see tail section |
| `0x1010cc3d4` | `%ebx` | Outer batch index (reloaded from `-0xe4(%rbp)`) | **Not** the `28*index` `%rbx` at draw sites |
| `0x1010cbe4f` | `-0xe4(%rbp)` | Saved outer index before inner walk | `movl %ebx, -0xe4` @ `0x1010cbe4f` |

**Resolved ambiguity:** `%ebx` at `0x1010cc3e2` is the **outer** batch counter (from `-0xe4`), not the per-record stride offset in `%rbx` used at `0x1010cc28c`–`0x1010cc2e8`.

## Structured types

### LoopContext (outer + hook-visible)

```text
mode                         # 0x24(%r12)
cached_color                 # -0x51(%rbp)
cached_vbo_index             # -0x64(%rbp)
bound_ibo_identity           # ZERO_BIND shadow only; ONE_BIND uses pre-multidraw GfxSetIndexBuffer
required_ibo_argument        # movq (%rax,%r15,8), %rsi @ 0x1010cc2c6 — pointer passed to GfxSetIndexBuffer
skip_or_visibility_mask      # %r13b (exact semantics: bit tested at 0x1(%rcx))
index_table_cursor           # -0x70(%rbp)
index_table_end              # -0x198(%rbp)
record_array_base            # %r14 after 0x1010cbe67
record_index                 # -0x80(%rbp) / %r15 at mode-0 draw
gfx_deferred_context         # -0x60(%rbp)
sborder_draw_info            # %r12
```

### IndexTableEntry (associated data at cursor — **not** `SBorderDraw`)

At hook `0x1010cbe55`, `%rcx` points into the index table:

```text
skip_flag_at_plus_1          # testb %r13b, 0x1(%rcx)
record_index_at_minus_2      # movzwl -0x2(%rcx), %ecx  (@ 0x1010cbe63)
color_byte_at_cursor         # movb (%rax), %cl when rax = cursor (@ 0x1010cc1d3)
```

**Forward-scan requirement:** when classifying record `i>0`, derive `%rcx` from the saved index cursor `-0x70(%rbp)` using the same arithmetic as loop tail `0x1010cc3bd`–`0x1010cc3da` (`addq $4, %rcx` per consumed entry; end compare via `leaq -0x2(%rcx), %rax` vs `-0x198(%rbp)`). Prefix simulation must mirror this cursor advance, not assume contiguous `SBorderDraw` memory alone.

### RecordView (`SBorderDraw`, 28-byte stride)

See [border-draw-record-layout.json](border-draw-record-layout.json): `+0x04` basevertex, `+0x06` triangle count, `+0x18` VBO table index.

## Pseudocode (inner walk)

```text
# Entry when r15 > 0 @ 0x1010cbe38
cursor = index_table[-0x70]
end_bound = cursor + 4 * r15
store end_bound -> -0x198
cursor += 2
cached_color = 0xFF  # movb -1, -0x51 @ 0x1010cbe4b
save outer ebx -> -0xe4

loop_head:
    if not (r13b & *(cursor+1)): goto loop_tail_one_step   # 0x1010cbe55
    record_index = uint16(cursor[-2])
    r14 = record_array_base from 0x78(r12)
    ... visibility / color setup when ebx <= 7 ...
    if mode(r12+0x24) != 0: goto site1_or_2
    # mode 0 @ 0x1010cc27d
    offset = 28 * record_index
    if record.vbo_index != cached_vbo: GfxSetVertexBuffers; update cached_vbo
    ibo = array[0x60(r12)][record_index]
    GfxSetIndexBuffer(ibo)
    GfxDrawIndexed(tri*3, basevertex, helper_arg=0)  # 0x1010cc2e8
    goto loop_tail_one_step

loop_tail_one_step:  # 0x1010cc3bd — advances ONE logical index step
    cursor = index_table[-0x70]
    if cursor+2 < end_bound: cursor += 4; goto loop_head
    else: outer_batch++; maybe next outer batch
```

## Hook placement

Preferred detour: **`0x1010cbe55`** — bytes `44 84 69 01 48 89 4d 90 0f 84 5a 05 00 00` (14-byte patch, RIP-indirect jmp). Classifier runs **before** `0x1010cbe63` record-index load and mode-0 setup.

**IBO shadow (`ZERO_BIND` only):** `ibo_known` becomes true after a proven successful `GfxSetIndexBuffer` transition on the prior drawable iteration — not after `GfxDrawIndexed`. Production topology in this PR is **`ONE_BIND`** (see helper audit).

**Walk end:** `walk_exhausted` mirrors tail compare to `-0x198(%rbp)`; distinct from scan-cap truncation.
