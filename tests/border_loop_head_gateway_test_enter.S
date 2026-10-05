.section __TEXT,__text
.globl _gateway_test_enter
.align 16
_gateway_test_enter:
    movq %rsp, %r10
    andq $-16, %rsp
    movq _gateway_test_seed(%rip), %rax
    movq _gateway_test_seed+8(%rip), %rcx
    movq _gateway_test_seed+16(%rip), %rdx
    movq _gateway_test_seed+24(%rip), %rsi
    movq _gateway_test_seed+32(%rip), %rdi
    movq _gateway_test_seed+40(%rip), %r8
    movq _gateway_test_seed+48(%rip), %r9
    movq _gateway_test_seed+56(%rip), %r10
    movq _gateway_test_seed+64(%rip), %r11
    ldmxcsr _gateway_test_seed+72(%rip)
    movups _gateway_test_seed+76(%rip), %xmm0
    movq _gateway_test_live_rbp(%rip), %rbp
    movq _gateway_test_live_r12(%rip), %r12
    movq _gateway_test_live_r13(%rip), %r13
    movq _gateway_test_live_rcx(%rip), %rcx
    jmp _eu4_border_loop_head_gateway
