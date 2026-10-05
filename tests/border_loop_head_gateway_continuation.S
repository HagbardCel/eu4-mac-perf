.section __TEXT,__text
.globl _gateway_test_continuation_draw
.globl _gateway_test_continuation_skip
.align 16

/* Capture machine state immediately after gateway restoration. */
.macro gateway_test_capture_state branch_id
    movq %rax, _gateway_test_capture(%rip)
    movq %rcx, _gateway_test_capture+8(%rip)
    movq %rdx, _gateway_test_capture+16(%rip)
    movq %rsi, _gateway_test_capture+24(%rip)
    movq %rdi, _gateway_test_capture+32(%rip)
    movq %r8, _gateway_test_capture+40(%rip)
    movq %r9, _gateway_test_capture+48(%rip)
    movq %r10, _gateway_test_capture+56(%rip)
    movq %r11, _gateway_test_capture+64(%rip)
    stmxcsr _gateway_test_capture+72(%rip)
    movups %xmm0, _gateway_test_capture+76(%rip)
    movl $\branch_id, _gateway_test_branch_id(%rip)
    subq $8, %rsp
    callq _gateway_test_longjmp_back
    addq $8, %rsp
    int3
.endm

_gateway_test_continuation_draw:
    gateway_test_capture_state 1

_gateway_test_continuation_skip:
    gateway_test_capture_state 2
