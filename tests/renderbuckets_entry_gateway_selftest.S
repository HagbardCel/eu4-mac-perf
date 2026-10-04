.text
.align 16
.globl _trampoline_insn_block
_trampoline_insn_block:
    /* Displaced 13-byte RenderBuckets prologue (eu4+0x14c7da4). */
    .byte 0x55
    .byte 0x48, 0x89, 0xe5
    .byte 0x41, 0x57
    .byte 0x41, 0x56
    .byte 0x41, 0x55
    .byte 0x41, 0x54
    .byte 0x53
    jmp _resume_after_entry_trampoline

.globl _eu4_renderbuckets_entry_gateway_transparency_selftest
_eu4_renderbuckets_entry_gateway_transparency_selftest:
    leaq _trampoline_insn_block(%rip), %rax
    movq %rax, _eu4_renderbuckets_trampoline_ptr(%rip)
    movl $0, _g_observer_calls(%rip)
    movl $0, _g_bad_stack_alignment(%rip)
    leaq _arg_rdi_marker(%rip), %rdi
    movq $0x42, %rsi
    movq $0x43, %rdx
    movq $0x44, %rcx
    movq $1, %r8
    movq $0, %r9
    callq _run_entry_gateway_once
    cmpq $0x42, %rsi
    jne _fail
    cmpq $0x43, %rdx
    jne _fail
    cmpq $0x44, %rcx
    jne _fail
    cmpq $1, %r8
    jne _fail
    cmpq $0, %r9
    jne _fail
    leaq _arg_rdi_marker(%rip), %rax
    cmpq %rax, %rdi
    jne _fail
    movl _g_observer_calls(%rip), %eax
    cmpl $1, %eax
    jne _fail
    movl _g_bad_stack_alignment(%rip), %eax
    cmpl $0, %eax
    jne _fail
    movl _g_saved_layer(%rip), %eax
    cmpl $0x44, %eax
    jne _fail
    movl _g_saved_arg_r8(%rip), %eax
    cmpl $1, %eax
    jne _fail
    movl _g_saved_arg_r9(%rip), %eax
    cmpl $0, %eax
    jne _fail
    /* Polluted high bits in r8/r9 must not affect captured booleans. */
    leaq _arg_rdi_marker(%rip), %rdi
    movq $0xff00000000000001, %r8
    movq $0xff00000000000000, %r9
    movl $0x55, %ecx
    callq _run_entry_gateway_once
    movl _g_saved_layer(%rip), %eax
    cmpl $0x55, %eax
    jne _fail
    movl _g_saved_arg_r8(%rip), %eax
    cmpl $1, %eax
    jne _fail
    movl _g_saved_arg_r9(%rip), %eax
    cmpl $0, %eax
    jne _fail
    movl _g_observer_calls(%rip), %eax
    cmpl $2, %eax
    jne _fail
    movl $0, %eax
    ret
_fail:
    movl $1, %eax
    ret

_run_entry_gateway_once:
    pushq %rbp
    movq %rsp, %rbp
    subq $0x80, %rsp
    jmp _eu4_renderbuckets_entry_gateway

_resume_after_entry_trampoline:
    popq %rbx
    popq %r12
    popq %r13
    popq %r14
    popq %r15
    popq %rbp
    addq $0x80, %rsp
    popq %rbp
    ret

.data
.globl _arg_rdi_marker
_arg_rdi_marker:
    .quad 0
