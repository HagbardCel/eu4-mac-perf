.section __DATA,__data

.macro OBS_GATEWAY_DATA name
.globl _eu4_gfx_observe_trampoline_\name
.align 3
_eu4_gfx_observe_trampoline_\name:
    .quad 0
.globl _eu4_gfx_observe_entry_\name
.align 3
_eu4_gfx_observe_entry_\name:
    .quad 0
.globl _eu4_gfx_observe_exit_\name
.align 3
_eu4_gfx_observe_exit_\name:
    .quad 0
.endm

OBS_GATEWAY_DATA 0
OBS_GATEWAY_DATA 1
OBS_GATEWAY_DATA 2
OBS_GATEWAY_DATA 3
OBS_GATEWAY_DATA 4
OBS_GATEWAY_DATA 5
OBS_GATEWAY_DATA 6
OBS_GATEWAY_DATA 7

.section __TEXT,__text

# Register-neutral call-through gateway. The relay reaches this code with a
# near jmp; the trampoline is reached through a RIP-indirect pointer. The slot
# index is passed to the hooks in %esi after the original registers are saved.
#
# Frame (648 bytes, RSP 16-byte aligned while it exists):
#   0..511   FXSAVE64 area
#   512..631 GPRs rax,rbx,rcx,rdx,rsi,rdi,rbp,r8..r15
#   632..639 padding
#   640..647 RFLAGS
.macro OBS_GATEWAY name, entry, exit, tramp, slot
.globl _eu4_gfx_observe_gateway_\name
.p2align 4
_eu4_gfx_observe_gateway_\name:
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
    movl $\slot, %esi
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
    movl $\slot, %esi
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

OBS_GATEWAY 0, _eu4_gfx_observe_entry_0, _eu4_gfx_observe_exit_0, _eu4_gfx_observe_trampoline_0, 0
OBS_GATEWAY 1, _eu4_gfx_observe_entry_1, _eu4_gfx_observe_exit_1, _eu4_gfx_observe_trampoline_1, 1
OBS_GATEWAY 2, _eu4_gfx_observe_entry_2, _eu4_gfx_observe_exit_2, _eu4_gfx_observe_trampoline_2, 2
OBS_GATEWAY 3, _eu4_gfx_observe_entry_3, _eu4_gfx_observe_exit_3, _eu4_gfx_observe_trampoline_3, 3
OBS_GATEWAY 4, _eu4_gfx_observe_entry_4, _eu4_gfx_observe_exit_4, _eu4_gfx_observe_trampoline_4, 4
OBS_GATEWAY 5, _eu4_gfx_observe_entry_5, _eu4_gfx_observe_exit_5, _eu4_gfx_observe_trampoline_5, 5
OBS_GATEWAY 6, _eu4_gfx_observe_entry_6, _eu4_gfx_observe_exit_6, _eu4_gfx_observe_trampoline_6, 6
OBS_GATEWAY 7, _eu4_gfx_observe_entry_7, _eu4_gfx_observe_exit_7, _eu4_gfx_observe_trampoline_7, 7
