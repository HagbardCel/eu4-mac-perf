/* One increment of the qword at rdi. No calls, so the instrumented section
 * stays inside this function. The other leaves are outside this section. */
    .section __TEXT,__tr3_scope,regular,pure_instructions
    .globl _tr3_subject
    .p2align 4
_tr3_subject:
    movq (%rdi), %rax
    incq %rax
    movq %rax, (%rdi)
    retq

    .section __TEXT,__text,regular,pure_instructions
    .globl _tr3_leaf_b, _tr3_leaf_b_end
    .p2align 4
_tr3_leaf_b:
    cmpq $0x111, %rax
    jne 1f
    movq $0x42, %rcx
    addq %rcx, %rbx
    movq %rbx, %xmm2
    jmp 2f
1:
    movq $7, %rcx
    xorq %rbx, %rbx
2:
    movq $0x1234, %r8
    retq
_tr3_leaf_b_end:

    .globl _tr3_calls, _tr3_calls_end
    .p2align 4
_tr3_calls:
    callq _tr3_ret_only
    retq
_tr3_calls_end:

    /* Five-byte jump so the entry patch stays inside this function. */
    .globl _tr3_jump_out, _tr3_jump_out_end
    .p2align 4
_tr3_jump_out:
    .byte 0xe9
    .long _tr3_ret_only - (. + 4)
_tr3_jump_out_end:

    .globl _tr3_ret_only
_tr3_ret_only:
    retq

    /* Slot is 16 GPRs, flags, 8 bytes of pad, then a 16-byte-aligned fxsave. */
    .macro save_state dest
    movq %rax, \dest+0(%rip)
    movq %rbx, \dest+8(%rip)
    movq %rcx, \dest+16(%rip)
    movq %rdx, \dest+24(%rip)
    movq %rsi, \dest+32(%rip)
    movq %rdi, \dest+40(%rip)
    movq %r8, \dest+48(%rip)
    movq %r9, \dest+56(%rip)
    movq %r10, \dest+64(%rip)
    movq %r11, \dest+72(%rip)
    movq %r12, \dest+80(%rip)
    movq %r13, \dest+88(%rip)
    movq %r14, \dest+96(%rip)
    movq %r15, \dest+104(%rip)
    movq %rbp, \dest+112(%rip)
    movq %rsp, \dest+120(%rip)
    pushfq
    popq %rax
    movq %rax, \dest+128(%rip)
    movq \dest+0(%rip), %rax
    fxsave64 \dest+144(%rip)
    .endm

    .globl _tr3_apply_state
    .p2align 4
_tr3_apply_state:
    leaq _tr3_gpr_a(%rip), %rax
    movq 8(%rax), %rbx
    movq 16(%rax), %rcx
    movq 24(%rax), %rdx
    movq 32(%rax), %rsi
    movq 40(%rax), %rdi
    movq 48(%rax), %r8
    movq 56(%rax), %r9
    movq 72(%rax), %r11
    movq 80(%rax), %r12
    movq 88(%rax), %r13
    movq 96(%rax), %r14
    movq 104(%rax), %r15
    movq 112(%rax), %rbp
    pushq $0x202
    popfq
    pxor %xmm2, %xmm2
    pxor %xmm3, %xmm3
    pxor %xmm4, %xmm4
    pxor %xmm5, %xmm5
    pxor %xmm6, %xmm6
    pxor %xmm7, %xmm7
    pxor %xmm8, %xmm8
    pxor %xmm9, %xmm9
    pxor %xmm10, %xmm10
    pxor %xmm11, %xmm11
    pxor %xmm12, %xmm12
    pxor %xmm13, %xmm13
    pxor %xmm14, %xmm14
    pxor %xmm15, %xmm15
    movq _tr3_xmm0(%rip), %r10
    movq %r10, %xmm0
    movq _tr3_xmm1(%rip), %r10
    movq %r10, %xmm1
    movq 64(%rax), %r10
    movq (%rax), %rax
    ldmxcsr _tr3_mxcsr(%rip)
    retq

    .globl _tr3_snap
    .p2align 4
_tr3_snap:
    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    movq _tr3_snap_n(%rip), %rax
    cmpq $0, %rax
    je 1f
    cmpq $1, %rax
    jne 3f
    callq _tr3_apply_state
    save_state _tr3_snap_entry1
    callq _tr3_leaf_b
    save_state _tr3_snap_exit1
    jmp 2f
1:
    callq _tr3_apply_state
    save_state _tr3_snap_entry0
    callq _tr3_leaf_b
    save_state _tr3_snap_exit0
2:
    incq _tr3_snap_n(%rip)
3:
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    retq

    .section __DATA,__const
    .p2align 2
_tr3_mxcsr:
    .long 0x1f80
    .p2align 3
_tr3_gpr_a:
    .quad 0x111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666
    .quad 0x7777, 0x8888, 0x9999, 0xaaaa, 0xbbbb, 0xcccc
    .quad 0xdddd, 0xeeee, 0x1111
_tr3_xmm0:
    .quad 0x0102030405060708
_tr3_xmm1:
    .quad 0xfedcba9876543210

    .section __DATA,__data
    .p2align 4
    .globl _tr3_snap_entry0, _tr3_snap_exit0, _tr3_snap_entry1, _tr3_snap_exit1
    .globl _tr3_snap_n
_tr3_snap_entry0:
    .space 656
_tr3_snap_exit0:
    .space 656
_tr3_snap_entry1:
    .space 656
_tr3_snap_exit1:
    .space 656
_tr3_snap_n:
    .quad 0
