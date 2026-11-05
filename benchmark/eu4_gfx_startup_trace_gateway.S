.section __DATA,__data
.globl _eu4_gfx_startup_trampoline_settings
.align 3
_eu4_gfx_startup_trampoline_settings:
    .quad 0
.globl _eu4_gfx_startup_entry_settings
.align 3
_eu4_gfx_startup_entry_settings:
    .quad 0
.globl _eu4_gfx_startup_exit_settings
.align 3
_eu4_gfx_startup_exit_settings:
    .quad 0
.globl _eu4_gfx_startup_trampoline_init
.align 3
_eu4_gfx_startup_trampoline_init:
    .quad 0
.globl _eu4_gfx_startup_entry_init
.align 3
_eu4_gfx_startup_entry_init:
    .quad 0
.globl _eu4_gfx_startup_exit_init
.align 3
_eu4_gfx_startup_exit_init:
    .quad 0

.section __TEXT,__text

.macro STARTUP_GATEWAY name, entry, exit, tramp
.globl \name
.p2align 4
\name:
    pushfq
    subq $640, %rsp

    movq %rax, 512(%rsp)
    movq %rbx, 520(%rsp)
    movq %rcx, 528(%rsp)
    movq %rdx, 536(%rsp)
    movq %rsi, 544(%rsp)
    movq %rdi, 552(%rsp)
    movq %rbp, 560(%rsp)
    movq %r8, 568(%rsp)
    movq %r9, 576(%rsp)
    movq %r10, 584(%rsp)
    movq %r11, 592(%rsp)
    movq %r12, 600(%rsp)
    movq %r13, 608(%rsp)
    movq %r14, 616(%rsp)
    movq %r15, 624(%rsp)
    fxsave64 0(%rsp)

    movq \entry(%rip), %rax
    testq %rax, %rax
    jz 1f
    movq %rsp, %rdi
    callq *%rax
1:

    fxrstor64 0(%rsp)
    movq 512(%rsp), %rax
    movq 520(%rsp), %rbx
    movq 528(%rsp), %rcx
    movq 536(%rsp), %rdx
    movq 544(%rsp), %rsi
    movq 552(%rsp), %rdi
    movq 560(%rsp), %rbp
    movq 568(%rsp), %r8
    movq 576(%rsp), %r9
    movq 584(%rsp), %r10
    movq 592(%rsp), %r11
    movq 600(%rsp), %r12
    movq 608(%rsp), %r13
    movq 616(%rsp), %r14
    movq 624(%rsp), %r15
    pushq 640(%rsp)
    popfq

    callq *\tramp(%rip)

    movq %rax, 512(%rsp)
    movq %rbx, 520(%rsp)
    movq %rcx, 528(%rsp)
    movq %rdx, 536(%rsp)
    movq %rsi, 544(%rsp)
    movq %rdi, 552(%rsp)
    movq %rbp, 560(%rsp)
    movq %r8, 568(%rsp)
    movq %r9, 576(%rsp)
    movq %r10, 584(%rsp)
    movq %r11, 592(%rsp)
    movq %r12, 600(%rsp)
    movq %r13, 608(%rsp)
    movq %r14, 616(%rsp)
    movq %r15, 624(%rsp)
    pushfq
    popq %rax
    movq %rax, 640(%rsp)
    fxsave64 0(%rsp)

    movq \exit(%rip), %rax
    testq %rax, %rax
    jz 2f
    movq %rsp, %rdi
    callq *%rax
2:

    fxrstor64 0(%rsp)
    movq 512(%rsp), %rax
    movq 520(%rsp), %rbx
    movq 528(%rsp), %rcx
    movq 536(%rsp), %rdx
    movq 544(%rsp), %rsi
    movq 552(%rsp), %rdi
    movq 560(%rsp), %rbp
    movq 568(%rsp), %r8
    movq 576(%rsp), %r9
    movq 584(%rsp), %r10
    movq 592(%rsp), %r11
    movq 600(%rsp), %r12
    movq 608(%rsp), %r13
    movq 616(%rsp), %r14
    movq 624(%rsp), %r15
    addq $640, %rsp
    popfq
    ret
.endm

STARTUP_GATEWAY _eu4_gfx_startup_gateway_settings, _eu4_gfx_startup_entry_settings, _eu4_gfx_startup_exit_settings, _eu4_gfx_startup_trampoline_settings
STARTUP_GATEWAY _eu4_gfx_startup_gateway_init, _eu4_gfx_startup_entry_init, _eu4_gfx_startup_exit_init, _eu4_gfx_startup_trampoline_init
