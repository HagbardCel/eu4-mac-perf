.section __DATA,__data
.globl _eu4_mesh_trampoline_ptr
.align 3
_eu4_mesh_trampoline_ptr:
    .quad 0

.section __TEXT,__text
.globl _eu4_mesh_observer_gateway

/* Preserve caller-saved GPRs + RFLAGS, call observer, replay displaced insns via trampoline. */
_eu4_mesh_observer_gateway:
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
    movq %rbp, %rdi
    movl %r12d, %esi
    movzbl -0xac(%rbp), %edx
    callq _eu4_submission_mesh_site_from_frame
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
    jmpq *_eu4_mesh_trampoline_ptr(%rip)
