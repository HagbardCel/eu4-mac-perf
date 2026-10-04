# RenderBuckets entry ABI — v3.1 authoritative context

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)  
**Symbol:** `_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb`  
**Entry:** `0x14c7da4` ([`renderbuckets-entry-hook-site.json`](renderbuckets-entry-hook-site.json))

## SysV AMD64 at function entry (after gateway, before prologue replay)

The entry hook runs immediately after the displaced 13-byte prologue save sequence. Original arguments are still in the standard registers:

| Register | Documented role | v3.1 TLS field |
|----------|-----------------|----------------|
| `rcx` (low 32) | `int layer` | `entry_layer` |
| `r8b` | `bool` argument A | `entry_arg_r8_bool` |
| `r9b` | `bool` argument B | `entry_arg_r9_bool` |

Gateway captures with `movl %ecx,%edi`, `movzbl %r8b,%esi`, `movzbl %r9b,%edx` so only the low boolean byte is semantic.

## Flush-array selector linkage (static)

On the mesh subrecord path, opaque vs transparent flush selection uses the **invocation’s** boolean argument in `r9` (low byte). Static map:

1. Entry hook site JSON lists `r9: bool` as the third boolean parameter to `RenderBuckets`.
2. Mesh site hook region (`0x14c8181` gate, documented in [`cross-parent-buffer-bind-re.md`](cross-parent-buffer-bind-re.md)) chooses flush array **before** site hook `0x14c81e6` using that invocation-fixed kind — not a per-subrecord reinterpretation of `%r12` at the hook.
3. v3.0 used `-0xac(%rbp)` and `%r12d` at `0x14c81e6`; Venice v3.0 showed those values drift within a single epoch → **v3.1 comparators use TLS entry `entry_arg_r9_bool` as `flush_array_kind`**, not mid-loop stack/register copies.

**Comparator rule:** for all site entries sharing TLS epoch `E`, `eu4_subrecord_context_t.layer_index` and `flush_array_kind` are the entry snapshot for `E`.

## Non-static caveat

Full Itanium ABI name proves member function `CPdxMeshObject::RenderBuckets`; `this` is in `rdi` and is **not** part of invocation layer/flush context. No recursion observed on this symbol for the Venice mesh path.
