.section __DATA,__data
.globl _eu4_mesh_trampoline_ptr
.align 3
_eu4_mesh_trampoline_ptr:
    .quad 0

.section __TEXT,__text
.globl _eu4_mesh_observer_gateway
_eu4_mesh_observer_gateway:
    pushq %rbp
    movq %rbp, %rdi
    callq _eu4_submission_mesh_site_from_rbp
    popq %rbp
    jmpq *_eu4_mesh_trampoline_ptr(%rip)
