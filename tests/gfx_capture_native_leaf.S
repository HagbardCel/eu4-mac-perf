.section __DATA,__data
.globl _gfx_capture_native_leaf_calls
.align 3
_gfx_capture_native_leaf_calls:
    .quad 0

.section __TEXT,__text
.globl _gfx_capture_native_leaf
.p2align 4
_gfx_capture_native_leaf:
    pushq %rbp
    movq %rsp, %rbp
    movq (%rdi), %rax
    movl %ecx, %ecx
    cmpl $-1, %r9d

    leaq 1(%rax), %rax
    addq $2, %rcx
    xorq %rdx, %rdx
    addq $3, %r9
    testq %r8, %r8
    pushfq
    incq _gfx_capture_native_leaf_calls(%rip)
    popfq

    popq %rbp
    ret
