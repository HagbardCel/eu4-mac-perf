# RenderBuckets entry ABI — v3.1 authoritative context

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)  
**Symbol:** `_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb`  
**Detour / entry:** `0x14c7da4` ([`renderbuckets-entry-hook-site.json`](renderbuckets-entry-hook-site.json))

## Detour timing

The detour transfers control to the observer gateway at `0x14c7da4` **before** the displaced 13-byte prologue executes. The gateway reads the original argument registers while they are still the caller’s values; the observer runs; the trampoline **then** replays the displaced prologue and resumes at `0x14c7db1`.

## SysV AMD64 argument registers at gateway entry

| Register | Role (pinned contract) | v3.1 TLS field |
|----------|------------------------|----------------|
| `rcx` (low 32) | `int layer` | `entry_layer` |
| `r8b` | `bool` argument A | `entry_arg_r8_bool` |
| `r9b` | `bool` argument B | `entry_arg_r9_bool` |

See [`renderbuckets-entry-hook-site.json`](renderbuckets-entry-hook-site.json) `sysv_arguments_preserved`. Gateway: `movl %ecx,%edi`, `movzbl %r8b,%esi`, `movzbl %r9b,%edx`.

Register mapping for comparators is justified by prologue/spill instructions at `0x14c7db8` and `0x14c7dc6` in the pinned binary ([`mesh-subrecord-hook-site.json`](analysis/mesh-subrecord-hook-site.json) `context_at_insertion`), not by parsing the Itanium mangled name.

## Flush-array selector linkage (static)

| Claim | Pinned evidence |
|-------|-----------------|
| Layer at entry → `%r12d` later in function | `0x14c7dc6: movl %ecx, %r12d` |
| Invocation flush selector in `%r9` low byte | `0x14c7db8: movl %r9d, -0xac(%rbp)` |
| Opaque/transparent gate uses that stack byte | `0x14c8181: cmpb -0xac(%rbp), %cl` |

v3.1 comparators use TLS entry `entry_layer` and `entry_arg_r9_bool` as `flush_array_kind` for all site entries in the same RenderBuckets epoch — not `%r12d` or `-0xac(%rbp)` at mesh site hook `0x14c81e6` (v3.0 Venice showed mid-loop drift).

**Comparator rule:** for TLS epoch `E`, `eu4_subrecord_context_t.layer_index` and `flush_array_kind` are the entry snapshot for `E`.
