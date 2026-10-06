.section __DATA,__data
.globl _border_loop_draw_target
.align 3
_border_loop_draw_target:
    .quad 0
.globl _border_loop_skip_target
.align 3
_border_loop_skip_target:
    .quad 0

.extern _g_eu4_border_observer_active

.section __TEXT,__text
.globl _eu4_border_loop_head_gateway
.align 16
_eu4_border_loop_head_gateway:
    cmpb $0, _g_eu4_border_observer_active(%rip)
    jne active_observer

    testb %r13b, 0x1(%rcx)
    movq %rcx, -0x70(%rbp)
    jne 1f
    jmpq *_border_loop_skip_target(%rip)
1:
    jmpq *_border_loop_draw_target(%rip)

active_observer:
    pushq %rax
    pushq %rcx
    pushq %rdx
    pushq %rsi
    pushq %rdi
    pushq %r8
    pushq %r9
    pushq %r10
    pushq %r11
    pushfq
    subq $512, %rsp
    fxsave64 (%rsp)
    movq %rbp, %rdi
    movq %r12, %rsi
    movq %r13, %rdx
    movq %rcx, %rcx
    callq _eu4_border_loop_head_observe_frame
    fxrstor64 (%rsp)
    addq $512, %rsp
    popfq
    popq %r11
    popq %r10
    popq %r9
    popq %r8
    popq %rdi
    popq %rsi
    popq %rdx
    popq %rcx
    popq %rax
    testb %r13b, 0x1(%rcx)
    movq %rcx, -0x70(%rbp)
    jne 2f
    jmpq *_border_loop_skip_target(%rip)
2:
    jmpq *_border_loop_draw_target(%rip)
