.section __DATA,__data
.globl _eu4_gfx_capture_trampoline_ptr
.align 3
_eu4_gfx_capture_trampoline_ptr:
    .quad 0
.globl _eu4_gfx_capture_entry_ptr
.align 3
_eu4_gfx_capture_entry_ptr:
    .quad 0
.globl _eu4_gfx_capture_exit_ptr
.align 3
_eu4_gfx_capture_exit_ptr:
    .quad 0

.section __TEXT,__text

# Register-neutral call-through gateway. The relay reaches this code with a
# near jmp; the trampoline is reached through a RIP-indirect pointer, so no
# register (in particular R11) is used for control transfer.
#
# Frame (648 bytes, RSP 16-byte aligned while it exists):
#   0..511   FXSAVE64 area
#   512..631 GPRs rax,rbx,rcx,rdx,rsi,rdi,rbp,r8..r15
#   632..639 padding
#   640..647 RFLAGS
.globl _eu4_gfx_capture_gateway
.p2align 4
_eu4_gfx_capture_gateway:
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

    movq _eu4_gfx_capture_entry_ptr(%rip), %rax
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

    callq *_eu4_gfx_capture_trampoline_ptr(%rip)

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

    movq _eu4_gfx_capture_exit_ptr(%rip), %rax
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
