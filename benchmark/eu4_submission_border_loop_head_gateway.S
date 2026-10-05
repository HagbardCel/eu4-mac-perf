.text
.globl _eu4_border_loop_head_gateway
.align 16
_eu4_border_loop_head_gateway:
    pushq %r11
    movq %rcx, %rdi
    movb %r13b, %sil
    callq _eu4_border_loop_head_gateway_from_regs
    popq %r11
    jmpq *_eu4_border_loop_head_trampoline_ptr(%rip)

.globl _eu4_border_loop_head_trampoline_ptr
_eu4_border_loop_head_trampoline_ptr:
    .quad 0
