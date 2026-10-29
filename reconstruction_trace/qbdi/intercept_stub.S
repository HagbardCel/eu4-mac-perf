/* Entry hook target for tr0_chase. Saves the natural caller's state into a
 * QBDI GPRState/FPRState, runs tr0_intercept_dispatch on a private host stack
 * (the guest keeps the caller's stack), then resumes the caller from the
 * state QBDI left at the return address. Offsets follow QBDI's x86-64
 * GPRState: rax 0, rbx 8, rcx 16, rdx 24, rsi 32, rdi 40, r8..r15 48..104,
 * rbp 112, rsp 120, rip 128, eflags 136. */

    .text
    .globl _tr0_intercept_stub
    .p2align 4
_tr0_intercept_stub:
    movq %rax, _tr0_entry_gpr+0(%rip)
    movq %rbx, _tr0_entry_gpr+8(%rip)
    movq %rcx, _tr0_entry_gpr+16(%rip)
    movq %rdx, _tr0_entry_gpr+24(%rip)
    movq %rsi, _tr0_entry_gpr+32(%rip)
    movq %rdi, _tr0_entry_gpr+40(%rip)
    movq %r8, _tr0_entry_gpr+48(%rip)
    movq %r9, _tr0_entry_gpr+56(%rip)
    movq %r10, _tr0_entry_gpr+64(%rip)
    movq %r11, _tr0_entry_gpr+72(%rip)
    movq %r12, _tr0_entry_gpr+80(%rip)
    movq %r13, _tr0_entry_gpr+88(%rip)
    movq %r14, _tr0_entry_gpr+96(%rip)
    movq %r15, _tr0_entry_gpr+104(%rip)
    movq %rbp, _tr0_entry_gpr+112(%rip)
    movq %rsp, _tr0_entry_gpr+120(%rip)
    leaq _tr0_chase(%rip), %rax
    movq %rax, _tr0_entry_gpr+128(%rip)
    pushfq
    popq %rax
    movq %rax, _tr0_entry_gpr+136(%rip)
    fxsave64 _tr0_entry_fpr(%rip)

    leaq _tr0_host_stack+4194304(%rip), %rsp
    callq _tr0_intercept_dispatch

    fxrstor64 _tr0_exit_fpr(%rip)
    movq _tr0_exit_gpr+8(%rip), %rbx
    movq _tr0_exit_gpr+80(%rip), %r12
    movq _tr0_exit_gpr+88(%rip), %r13
    movq _tr0_exit_gpr+96(%rip), %r14
    movq _tr0_exit_gpr+104(%rip), %r15
    movq _tr0_exit_gpr+112(%rip), %rbp
    movq _tr0_exit_gpr+120(%rip), %rsp
    movq _tr0_exit_gpr+0(%rip), %rax
    movq _tr0_exit_gpr+24(%rip), %rdx
    jmpq *_tr0_exit_gpr+128(%rip)
