.section __DATA,__data
.globl _border_loop_gateway_test_captured_rsp
.align 3
_border_loop_gateway_test_captured_rsp:
    .quad 0

.globl _gateway_test_poison_mxcsr
.align 4
_gateway_test_poison_mxcsr:
    .long 0x9FC0

.section __TEXT,__text
.globl _eu4_border_loop_head_observe_frame
.align 16
_eu4_border_loop_head_observe_frame:
    movq %rsp, _border_loop_gateway_test_captured_rsp(%rip)
    subq $8, %rsp
    callq _border_loop_gateway_test_observe_body
    addq $8, %rsp
    movabsq $0x1111111111111111, %rax
    movabsq $0x2222222222222222, %rcx
    movabsq $0x3333333333333333, %rdx
    movabsq $0x4444444444444444, %rsi
    movabsq $0x5555555555555555, %rdi
    movabsq $0x6666666666666666, %r8
    movabsq $0x7777777777777777, %r9
    movabsq $0x8888888888888888, %r10
    movabsq $0x9999999999999999, %r11
    pxor %xmm0, %xmm0
    ldmxcsr _gateway_test_poison_mxcsr(%rip)
    ret
