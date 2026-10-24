.section __DATA,__data
.globl _gfx_capture_test_saved_c_stack
.align 3
_gfx_capture_test_saved_c_stack:
    .quad 0

.align 4
_gfx_capture_test_stack:
    .space 8192
_gfx_capture_test_stack_top:

.section __TEXT,__text
.globl _gfx_capture_test_call_target
.p2align 4
_gfx_capture_test_call_target:
    pushq %rbx
    pushq %rbp
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    movq %rsp, _gfx_capture_test_saved_c_stack(%rip)
    leaq _gfx_capture_test_stack_top(%rip), %rsp

    pushq _gfx_capture_test_seed+120(%rip)
    popfq

    movups _gfx_capture_test_seed+136(%rip), %xmm0
    movups _gfx_capture_test_seed+152(%rip), %xmm1
    movups _gfx_capture_test_seed+168(%rip), %xmm2
    movups _gfx_capture_test_seed+184(%rip), %xmm3
    movups _gfx_capture_test_seed+200(%rip), %xmm4
    movups _gfx_capture_test_seed+216(%rip), %xmm5
    movups _gfx_capture_test_seed+232(%rip), %xmm6
    movups _gfx_capture_test_seed+248(%rip), %xmm7
    movups _gfx_capture_test_seed+264(%rip), %xmm8
    movups _gfx_capture_test_seed+280(%rip), %xmm9
    movups _gfx_capture_test_seed+296(%rip), %xmm10
    movups _gfx_capture_test_seed+312(%rip), %xmm11
    movups _gfx_capture_test_seed+328(%rip), %xmm12
    movups _gfx_capture_test_seed+344(%rip), %xmm13
    movups _gfx_capture_test_seed+360(%rip), %xmm14
    movups _gfx_capture_test_seed+376(%rip), %xmm15

    movq _gfx_capture_test_seed+0(%rip), %rax
    movq _gfx_capture_test_seed+8(%rip), %rbx
    movq _gfx_capture_test_seed+16(%rip), %rcx
    movq _gfx_capture_test_seed+24(%rip), %rdx
    movq _gfx_capture_test_seed+32(%rip), %rsi
    movq _gfx_capture_test_seed+40(%rip), %rdi
    movq _gfx_capture_test_seed+48(%rip), %rbp
    movq _gfx_capture_test_seed+56(%rip), %r8
    movq _gfx_capture_test_seed+64(%rip), %r9
    movq _gfx_capture_test_seed+72(%rip), %r10
    movq _gfx_capture_test_seed+80(%rip), %r11
    movq _gfx_capture_test_seed+88(%rip), %r12
    movq _gfx_capture_test_seed+96(%rip), %r13
    movq _gfx_capture_test_seed+104(%rip), %r14
    movq _gfx_capture_test_seed+112(%rip), %r15

    movq %rsp, _gfx_capture_test_pre_call_rsp(%rip)
    callq *_gfx_capture_test_target(%rip)

    movq %rsp, _gfx_capture_test_capture_rsp(%rip)
    pushfq
    popq _gfx_capture_test_capture+120(%rip)
    stmxcsr _gfx_capture_test_capture+128(%rip)
    movups %xmm0, _gfx_capture_test_capture+136(%rip)
    movups %xmm1, _gfx_capture_test_capture+152(%rip)
    movups %xmm2, _gfx_capture_test_capture+168(%rip)
    movups %xmm3, _gfx_capture_test_capture+184(%rip)
    movups %xmm4, _gfx_capture_test_capture+200(%rip)
    movups %xmm5, _gfx_capture_test_capture+216(%rip)
    movups %xmm6, _gfx_capture_test_capture+232(%rip)
    movups %xmm7, _gfx_capture_test_capture+248(%rip)
    movups %xmm8, _gfx_capture_test_capture+264(%rip)
    movups %xmm9, _gfx_capture_test_capture+280(%rip)
    movups %xmm10, _gfx_capture_test_capture+296(%rip)
    movups %xmm11, _gfx_capture_test_capture+312(%rip)
    movups %xmm12, _gfx_capture_test_capture+328(%rip)
    movups %xmm13, _gfx_capture_test_capture+344(%rip)
    movups %xmm14, _gfx_capture_test_capture+360(%rip)
    movups %xmm15, _gfx_capture_test_capture+376(%rip)
    movq %rax, _gfx_capture_test_capture+0(%rip)
    movq %rbx, _gfx_capture_test_capture+8(%rip)
    movq %rcx, _gfx_capture_test_capture+16(%rip)
    movq %rdx, _gfx_capture_test_capture+24(%rip)
    movq %rsi, _gfx_capture_test_capture+32(%rip)
    movq %rdi, _gfx_capture_test_capture+40(%rip)
    movq %rbp, _gfx_capture_test_capture+48(%rip)
    movq %r8, _gfx_capture_test_capture+56(%rip)
    movq %r9, _gfx_capture_test_capture+64(%rip)
    movq %r10, _gfx_capture_test_capture+72(%rip)
    movq %r11, _gfx_capture_test_capture+80(%rip)
    movq %r12, _gfx_capture_test_capture+88(%rip)
    movq %r13, _gfx_capture_test_capture+96(%rip)
    movq %r14, _gfx_capture_test_capture+104(%rip)
    movq %r15, _gfx_capture_test_capture+112(%rip)

    movq _gfx_capture_test_saved_c_stack(%rip), %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbp
    popq %rbx
    ret
