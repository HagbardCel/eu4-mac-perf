/*
 * Entry stub for the synthetic invocation harness.
 *
 * Switches to the declared stack so that the subject's return slot sits at the
 * declared entry RSP, loads every general-purpose register from
 * trace_harness_entry, calls trace_harness_target, then stores every register
 * (including the RSP the subject returned with) into trace_harness_exit and
 * restores the C stack and callee-saved registers.
 *
 * trace_harness_state layout: rax rbx rcx rdx rsi rdi rbp rsp r8 .. r15.
 */
.section __DATA,__data
.align 3
trace_harness_saved_c_stack:
    .quad 0

.section __TEXT,__text
.globl _trace_harness_enter
.p2align 4
_trace_harness_enter:
    pushq %rbx
    pushq %rbp
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    movq %rsp, trace_harness_saved_c_stack(%rip)

    movq _trace_harness_entry+56(%rip), %rsp
    addq $8, %rsp
    cld

    movq _trace_harness_entry+0(%rip), %rax
    movq _trace_harness_entry+8(%rip), %rbx
    movq _trace_harness_entry+16(%rip), %rcx
    movq _trace_harness_entry+24(%rip), %rdx
    movq _trace_harness_entry+32(%rip), %rsi
    movq _trace_harness_entry+40(%rip), %rdi
    movq _trace_harness_entry+48(%rip), %rbp
    movq _trace_harness_entry+64(%rip), %r8
    movq _trace_harness_entry+72(%rip), %r9
    movq _trace_harness_entry+80(%rip), %r10
    movq _trace_harness_entry+88(%rip), %r11
    movq _trace_harness_entry+96(%rip), %r12
    movq _trace_harness_entry+104(%rip), %r13
    movq _trace_harness_entry+112(%rip), %r14
    movq _trace_harness_entry+120(%rip), %r15

    callq *_trace_harness_target(%rip)

    movq %rax, _trace_harness_exit+0(%rip)
    movq %rbx, _trace_harness_exit+8(%rip)
    movq %rcx, _trace_harness_exit+16(%rip)
    movq %rdx, _trace_harness_exit+24(%rip)
    movq %rsi, _trace_harness_exit+32(%rip)
    movq %rdi, _trace_harness_exit+40(%rip)
    movq %rbp, _trace_harness_exit+48(%rip)
    movq %rsp, _trace_harness_exit+56(%rip)
    movq %r8, _trace_harness_exit+64(%rip)
    movq %r9, _trace_harness_exit+72(%rip)
    movq %r10, _trace_harness_exit+80(%rip)
    movq %r11, _trace_harness_exit+88(%rip)
    movq %r12, _trace_harness_exit+96(%rip)
    movq %r13, _trace_harness_exit+104(%rip)
    movq %r14, _trace_harness_exit+112(%rip)
    movq %r15, _trace_harness_exit+120(%rip)

    movq trace_harness_saved_c_stack(%rip), %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbp
    popq %rbx
    ret
