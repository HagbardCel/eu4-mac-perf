.section __DATA,__data
.globl _border_loop_gateway_test_captured_rsp
.align 3
_border_loop_gateway_test_captured_rsp:
    .quad 0

.section __TEXT,__text
.globl _eu4_border_loop_head_observe_frame
.align 16
_eu4_border_loop_head_observe_frame:
    movq %rsp, _border_loop_gateway_test_captured_rsp(%rip)
    callq _border_loop_gateway_test_observe_body
    ret
