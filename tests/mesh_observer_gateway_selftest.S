.text
.align 16
.globl _trampoline_insn_block
_trampoline_insn_block:
    /* Displaced instructions from eu4+0x14c81e6 (13 bytes). */
    .byte 0x48, 0x0f, 0x45, 0xc8
    .byte 0x8b, 0x31
    .byte 0x48, 0x8b, 0xbd, 0x20, 0xff, 0xff, 0xff
    jmp _resume_after_trampoline

.globl _eu4_mesh_gateway_transparency_selftest
_eu4_mesh_gateway_transparency_selftest:
    leaq _trampoline_insn_block(%rip), %rax
    movq %rax, _eu4_mesh_trampoline_ptr(%rip)
    /* ZF=0: cmovne should replace rcx with rax. */
    movl $0x11111111, _effect_main(%rip)
    movl $0x22222222, _effect_alt(%rip)
    leaq _effect_main(%rip), %rcx
    leaq _effect_alt(%rip), %rax
    movq $0, %r9
    callq _run_gateway_once
    cmpl $0x22222222, %esi
    jne _fail
    leaq _expected_rdi_value(%rip), %rdx
    cmpq (%rdx), %rdi
    jne _fail
    /* ZF=1: cmovne must leave rcx pointing at main effect. */
    leaq _effect_main(%rip), %rcx
    leaq _effect_alt(%rip), %rax
    movq $1, %r9
    callq _run_gateway_once
    cmpl $0x11111111, %esi
    jne _fail
    leaq _expected_rdi_value(%rip), %rdx
    cmpq (%rdx), %rdi
    jne _fail
    movl $0, %eax
    ret
_fail:
    movl $1, %eax
    ret

_run_gateway_once:
    pushq %rbp
    movq %rsp, %rbp
    subq $0x200, %rsp
    leaq _expected_rdi_value(%rip), %rdx
    movq (%rdx), %rdx
    movq %rdx, -0xe0(%rbp)
    movb $0, -0xac(%rbp)
    movl $0, %r12d
    testq %r9, %r9
    jz _set_zf_clear
    xorq %r8, %r8
    testq %r8, %r8
    jmp _enter_gateway
_set_zf_clear:
    movq $1, %r8
    cmpq $2, %r8
_enter_gateway:
    jmp _eu4_mesh_observer_gateway
_resume_after_trampoline:
    addq $0x200, %rsp
    popq %rbp
    ret

.data
.globl _effect_main
_effect_main:
    .long 0
.globl _effect_alt
_effect_alt:
    .long 0
.globl _expected_rdi_value
_expected_rdi_value:
    .quad 0x3333333333333333
