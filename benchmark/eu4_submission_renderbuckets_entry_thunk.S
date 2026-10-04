.section __DATA,__data
.globl _eu4_renderbuckets_trampoline_ptr
.align 3
_eu4_renderbuckets_trampoline_ptr:
    .quad 0

.section __TEXT,__text
.globl _eu4_renderbuckets_entry_gateway
_eu4_renderbuckets_entry_gateway:
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
    callq _eu4_submission_on_renderbuckets_entry
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
    jmpq *_eu4_renderbuckets_trampoline_ptr(%rip)
