.text
.globl _eu4_submission_on_renderbuckets_entry
_eu4_submission_on_renderbuckets_entry:
    movq %rsp, %rax
    andq $15, %rax
    cmpq $8, %rax
    jne _observer_bad_align
    movq %rdi, _g_saved_rdi(%rip)
    movq %rsi, _g_saved_rsi(%rip)
    movq %rdx, _g_saved_rdx(%rip)
    movq %rcx, _g_saved_rcx(%rip)
    movq %r8, _g_saved_r8(%rip)
    movq %r9, _g_saved_r9(%rip)
    pushfq
    popq %rax
    movq %rax, _g_saved_rflags(%rip)
    xorq %rax, %rax
    xorq %rcx, %rcx
    xorq %rdx, %rdx
    xorq %rsi, %rsi
    xorq %rdi, %rdi
    xorq %r8, %r8
    xorq %r9, %r9
    xorq %r10, %r10
    xorq %r11, %r11
    pushfq
    popq %rax
    xorq %rax, %rax
    pushq %rax
    popfq
    movl _g_observer_calls(%rip), %eax
    addl $1, %eax
    movl %eax, _g_observer_calls(%rip)
    ret
_observer_bad_align:
    movl $1, _g_bad_stack_alignment(%rip)
    ret

.data
.globl _g_observer_calls
_g_observer_calls:
    .long 0
.globl _g_bad_stack_alignment
_g_bad_stack_alignment:
    .long 0
.globl _g_saved_rdi
_g_saved_rdi:
    .quad 0
.globl _g_saved_rsi
_g_saved_rsi:
    .quad 0
.globl _g_saved_rdx
_g_saved_rdx:
    .quad 0
.globl _g_saved_rcx
_g_saved_rcx:
    .quad 0
.globl _g_saved_r8
_g_saved_r8:
    .quad 0
.globl _g_saved_r9
_g_saved_r9:
    .quad 0
.globl _g_saved_rflags
_g_saved_rflags:
    .quad 0
