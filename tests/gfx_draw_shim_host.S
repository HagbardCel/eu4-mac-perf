/* Calls the draw shim with known registers and checks them from the stub. */
    .text
    .globl _main
    .globl _eu4_gfx_shim_expect_ret
    .data
    .balign 8
_eu4_gfx_shim_expect_ret:
    .quad 0
    .text
_main:
    pushq %rbp
    movq %rsp, %rbp
    subq $192, %rsp
    xorl %ecx, %ecx
fill_stack:
    movb %cl, %al
    addb $0xa0, %al
    movb %al, (%rsp, %rcx)
    incl %ecx
    cmpl $64, %ecx
    jne fill_stack
    xorl %ecx, %ecx
fill_xmm:
    movb %cl, %al
    addb $0x10, %al
    movb %al, 128(%rsp, %rcx)
    incl %ecx
    cmpl $64, %ecx
    jne fill_xmm
    movdqu 128(%rsp), %xmm0
    movdqu 144(%rsp), %xmm1
    movdqu 160(%rsp), %xmm2
    movdqu 176(%rsp), %xmm3
    movdqu 128(%rsp), %xmm4
    movdqu 144(%rsp), %xmm5
    movdqu 160(%rsp), %xmm6
    movdqu 176(%rsp), %xmm7
    movq $0x1111111111110001, %rdi
    movq $0x2222222222220002, %rsi
    movq $0x3333333333330003, %rdx
    movq $0x4444444444440004, %rcx
    movq $0x5555555555550005, %r8
    movq $0x6666666666660006, %r9
    leaq Lret(%rip), %rax
    movq %rax, _eu4_gfx_shim_expect_ret(%rip)
    call _eu4_gfx_draw_entry
Lret:
    ud2
