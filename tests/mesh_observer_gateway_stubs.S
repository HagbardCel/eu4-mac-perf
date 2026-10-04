/* Adversarial SysV observer stub: clobber every caller-saved GPR and RFLAGS. */
.text
.globl _eu4_submission_mesh_site_from_frame
_eu4_submission_mesh_site_from_frame:
    movabs $0xdeadbeefdeadbeef, %rax
    movabs $0xcafebabecafebabe, %rcx
    movabs $0x1111222233334444, %rdx
    movabs $0x5555666677778888, %rsi
    movabs $0x9999aaaabbbbcccc, %rdi
    movabs $0xddddeeeeffff0000, %r8
    movabs $0x1212343456567878, %r9
    movabs $0x9abcdef09abcdef0, %r10
    movabs $0x0123456789abcdef, %r11
    pushfq
    popq %rax
    orq $0x400, %rax
    pushq %rax
    popfq
    ret
