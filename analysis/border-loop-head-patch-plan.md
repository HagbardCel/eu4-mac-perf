# Loop-head patch plan (design only)

**Detour site:** `0x1010cbe55`  
**Patch span:** 14 bytes (`testb` + `movq` + `je rel32`)  
**Encoding:** `RIP_INDIRECT_ABSOLUTE_JMP` (statically realizable; no rel32 island required)

Runtime code-page modification / icache flush — **mutation PR only** (out of scope here).

## Flow (`ibo_bind_topology: ONE_BIND`)

```text
patched_loop_head (14-byte jmp → trampoline):

classifier_trampoline:
    if N or A (mutation off): run displaced testb/movq/je; continue original
    if deferred_attrib_upload_pending or secondary_upload_pending: fall through one draw
    prefix = classify_batchable_prefix(...)
    if not prefix.batch_eligible: fall through one record (displaced path)
    GfxSetIndexBuffer(prefix.batch_key.ibo_argument)   # once; rdi = -0x60(%rbp) deferred ctx
    glMultiDrawElements(GL_TRIANGLES, counts, GL_UNSIGNED_SHORT, indices[], drawcount)
    if prefix.termination_kind == WALK_END:
        jmp terminal_prefix_resume @ 0x1010cc3e0   # %rax/%rcx DEAD; do not synthesize
    else:
        set rcx = first-unconsumed entry; displaced testb+movq; je path
        jmp non_recursive entry (not patched 0x1010cbe55)
```

## Fail-closed

Unknown inputs, `INVALID`, `max_scan_steps` without policy, Gate 0 fail, helper preconditions → original loop.

## Scratch

TLS arrays for `count[]` and `indices[]` (pointer-sized zero offsets); **no** basevertex array. `max_scan_steps` cap separate from barrier telemetry (`SCAN_CAP`).

## Runtime Gate 0

Mutation PR must assert **`glMultiDrawElements`** (or resolved `_glewMultiDrawElements`) in the **active** gameplay GL context — not MDEBV-only. See evidence `runtime_preconditions` in [border-loop-head-feasibility-20261004.json](evidence/border-loop-head-feasibility-20261004.json).
