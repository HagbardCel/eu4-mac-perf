/* Calls the draw shim with known registers and checks them from the stub. */
    .text
    .globl _main
    .globl _eu4_gfx_shim_expect_ret
    .globl _eu4_gfx_shim_return
    .data
    .balign 8
_eu4_gfx_shim_expect_ret:
    .quad 0
_eu4_gfx_shim_return:
    .long 0
saved_rbp:
    .quad 0
saved_rsp:
    .quad 0
    .text
    .macro prep_args
    xorl %ecx, %ecx
1:
    movb %cl, %al
    addb $$0xa0, %al
    movb %al, (%rsp, %rcx)
    incl %ecx
    cmpl $$64, %ecx
    jne 1b
    xorl %ecx, %ecx
1:
    movb %cl, %al
    addb $$0x10, %al
    movb %al, 128(%rsp, %rcx)
    incl %ecx
    cmpl $$64, %ecx
    jne 1b
    movdqu 128(%rsp), %xmm0
    movdqu 144(%rsp), %xmm1
    movdqu 160(%rsp), %xmm2
    movdqu 176(%rsp), %xmm3
    movdqu 128(%rsp), %xmm4
    movdqu 144(%rsp), %xmm5
    movdqu 160(%rsp), %xmm6
    movdqu 176(%rsp), %xmm7
    movq $$0x1111111111110001, %rdi
    movq $$0x2222222222220002, %rsi
    movq $$0x3333333333330003, %rdx
    movq $$0x4444444444440004, %rcx
    movq $$0x5555555555550005, %r8
    movq $$0x6666666666660006, %r9
    .endm
_main:
    pushq %rbp
    movq %rsp, %rbp
    subq $192, %rsp
    movl $1, _eu4_gfx_shim_return(%rip)
    prep_args
    movq $0xa1a1a1a1a1a1a1a1, %rbx
    movq $0xa2a2a2a2a2a2a2a2, %r12
    movq $0xa3a3a3a3a3a3a3a3, %r13
    movq $0xa4a4a4a4a4a4a4a4, %r14
    movq $0xa5a5a5a5a5a5a5a5, %r15
    movq %rbp, saved_rbp(%rip)
    movq %rsp, saved_rsp(%rip)
    leaq Lback(%rip), %rax
    movq %rax, _eu4_gfx_shim_expect_ret(%rip)
    call _eu4_gfx_draw_entry
Lback:
    movq saved_rbp(%rip), %rax
    cmpq %rax, %rbp
    jne Lfail
    movq saved_rsp(%rip), %rax
    cmpq %rax, %rsp
    jne Lfail
    movabsq $0xa1a1a1a1a1a1a1a1, %rax
    cmpq %rax, %rbx
    jne Lfail
    movabsq $0xa2a2a2a2a2a2a2a2, %rax
    cmpq %rax, %r12
    jne Lfail
    movabsq $0xa3a3a3a3a3a3a3a3, %rax
    cmpq %rax, %r13
    jne Lfail
    movabsq $0xa4a4a4a4a4a4a4a4, %rax
    cmpq %rax, %r14
    jne Lfail
    movabsq $0xa5a5a5a5a5a5a5a5, %rax
    cmpq %rax, %r15
    jne Lfail
    xorl %ecx, %ecx
Lbytes:
    movb (%rsp, %rcx), %al
    movb %cl, %dl
    addb $0xa0, %dl
    cmpb %dl, %al
    jne Lfail
    incl %ecx
    cmpl $64, %ecx
    jne Lbytes
    movl $0, _eu4_gfx_shim_return(%rip)
    prep_args
    leaq Lret(%rip), %rax
    movq %rax, _eu4_gfx_shim_expect_ret(%rip)
    call _eu4_gfx_draw_entry
Lret:
    ud2
Lfail:
    movl $1, %edi
    call _exit
