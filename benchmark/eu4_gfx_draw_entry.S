/* Snapshot argument registers before any C function can spill them.
   Entered by jmp, so 8(%rbp) is the original return address after the frame. */
    .text
    .globl _eu4_gfx_draw_entry
_eu4_gfx_draw_entry:
    movl $1, %eax
    jmp Lcapture
    .globl _eu4_gfx_draw_indexed_entry
_eu4_gfx_draw_indexed_entry:
    movl $2, %eax
    jmp Lcapture
    .globl _eu4_gfx_draw_indexed_instanced_entry
_eu4_gfx_draw_indexed_instanced_entry:
    movl $3, %eax
    jmp Lcapture
Lcapture:
    pushq %rbp
    movq %rsp, %rbp
    subq $256, %rsp
    movl %eax, (%rsp)
    movl $0, 4(%rsp)
    movq %rdi, 8(%rsp)
    movq %rsi, 16(%rsp)
    movq %rdx, 24(%rsp)
    movq %rcx, 32(%rsp)
    movq %r8, 40(%rsp)
    movq %r9, 48(%rsp)
    movdqu %xmm0, 56(%rsp)
    movdqu %xmm1, 72(%rsp)
    movdqu %xmm2, 88(%rsp)
    movdqu %xmm3, 104(%rsp)
    movdqu %xmm4, 120(%rsp)
    movdqu %xmm5, 136(%rsp)
    movdqu %xmm6, 152(%rsp)
    movdqu %xmm7, 168(%rsp)
    movq 8(%rbp), %rax
    movq %rax, 184(%rsp)
    leaq 192(%rsp), %rdi
    leaq 16(%rbp), %rsi
    movq $64, %rcx
    rep movsb
    movq %rsp, %rdi
    call _eu4_gfx_draw_capture
    ud2
