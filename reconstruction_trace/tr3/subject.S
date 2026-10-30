/* One increment of the qword at rdi. No calls, so the instrumented section
 * stays inside this function. */
    .section __TEXT,__tr3_scope,regular,pure_instructions
    .globl _tr3_subject
    .p2align 4
_tr3_subject:
    movq (%rdi), %rax
    incq %rax
    movq %rax, (%rdi)
    retq
