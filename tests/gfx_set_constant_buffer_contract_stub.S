.section __TEXT,__text
.globl _gfx_set_constant_buffer_contract_stub
.p2align 4
_gfx_set_constant_buffer_contract_stub:
    pushq %rbp
    movq %rsp, %rbp
    movq (%rdi), %rax
    movl %ecx, %ecx
    cmpl $-1, %r9d
    je 1f
    leal 3(%r9), %edx
    addl $6, %r9d
    testl %edx, %edx
    cmovnsl %edx, %r9d
    sarl $2, %r9d
    jmp 2f
1:
    movl (%rsi), %r9d
    addl $3, %r9d
    shrl $2, %r9d
2:
    shlq $4, %rcx
    movl %r9d, 0xc8(%rax,%rcx)
    movq %rsi, 0xd0(%rax,%rcx)
    movb $1, 0x128(%rax)
    popq %rbp
    ret
