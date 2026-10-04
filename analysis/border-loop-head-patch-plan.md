# Loop-head patch plan (design only)

**Detour:** `0x1010cbe55` (`testb %r13b, 0x1(%rcx)` — 5 bytes typical)

## Flow

```text
patched_loop_head:
    jmp classifier_trampoline

classifier_trampoline:
    if minimal_hook(N) or mutate_disabled(A): execute displaced testb; jmp original_continue
    prefix = classify_batchable_prefix(...)
    if prefix.run_length < 2 or not prefix.batch_eligible:
        execute displaced testb; jmp original_continue   # fall through one record
    gather arrays from RecordView[0:prefix.run_length]
    if Gate0 and mutate_enabled(B): glMultiDrawElementsBaseVertex(...)
    synthesize_state_after_k_draws(prefix.run_length)
    jmp non_recursive_resume_before_record_k   # NOT patched 0x1010cbe55

original_continue:
    [displaced bytes from 0x1010cbe55]
    ... original engine path ...
```

## Non-recursive re-entry (Static GO #8)

Resume target **must not** be the patched entry that jumps to classifier. Use:

- **Displaced-instruction stub** executing original `testb` + fallthrough, or  
- TLS one-shot “already classified this iteration” (less preferred)

Document chosen mechanism in [border-hook-site.json](border-hook-site.json) when proven.

## Fail-closed

- Unknown classifier input → original loop head  
- `prefix.run_length` > TLS max → original loop  
- Gate 0 fail in B → original per-record draws  
- GfxDrawIndexed audit gaps → no mutation until closed

## Scratch

TLS/stack: `count[]`, `basevertex[]`, `indices[]` (zeros), max run cap TBD from stack budget.

**No dylib changes in feasibility PR.**
