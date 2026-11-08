.section __TEXT,__text

# 13-byte relocatable prologue matching the pinned GOG settings/init entries:
# push %rbp; mov %rbp,%rsp; push %r15; push %r14; push %r13; push %r12; push %rbx
.globl _gfx_startup_stub_settings
.p2align 4
_gfx_startup_stub_settings:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53
    # After the patched window.
    pushq %rbp
    movq %rsp, %rbp
    # rcx = settings. Mark first sample flag at +4.
    testq %rcx, %rcx
    jz 1f
    movq 8(%rcx), %rax
    testq %rax, %rax
    jz 1f
    movb $1, 4(%rax)
1:
    movl $7, %eax
    popq %rbp
    popq %rbx
    popq %r12
    popq %r13
    popq %r14
    popq %r15
    popq %rbp
    ret

.globl _gfx_startup_stub_init
.p2align 4
_gfx_startup_stub_init:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53
    pushq %rbp
    movq %rsp, %rbp
    # rsi = success byte destination. rdi unused for body.
    testq %rsi, %rsi
    jz 2f
    movb $1, (%rsi)
2:
    leaq _gfx_startup_stub_master(%rip), %rax
    popq %rbp
    popq %rbx
    popq %r12
    popq %r13
    popq %r14
    popq %r15
    popq %rbp
    ret

.globl _gfx_startup_stub_init_fail
.p2align 4
_gfx_startup_stub_init_fail:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53
    pushq %rbp
    movq %rsp, %rbp
    testq %rsi, %rsi
    jz 3f
    movb $0, (%rsi)
3:
    leaq _gfx_startup_stub_master(%rip), %rax
    popq %rbp
    popq %rbx
    popq %r12
    popq %r13
    popq %r14
    popq %r15
    popq %rbp
    ret

.section __DATA,__data
.globl _gfx_startup_stub_master
.align 4
_gfx_startup_stub_master:
    .space 0x198, 0

.section __TEXT,__text

# _GfxSetViewPort-shaped stub. Eight-byte complete prologue, 24-byte copy to
# master+0xac, then a tail jump to a GL-shaped stub that returns to the
# gateway continuation.
.globl _gfx_startup_stub_viewport
.p2align 4
_gfx_startup_stub_viewport:
    .byte 0x55, 0x48, 0x89, 0xe5, 0xf3, 0x0f, 0x6f, 0x06
    movdqu %xmm0, 0xac(%rdi)
    movl 0x10(%rsi), %eax
    movl %eax, 0xbc(%rdi)
    movl 0x14(%rsi), %esi
    movl %esi, 0xc0(%rdi)
    popq %rbp
    jmp _gfx_startup_stub_gl_viewport

.globl _gfx_startup_stub_gl_viewport
.p2align 4
_gfx_startup_stub_gl_viewport:
    ret

# A scalar-return stub with a different shape: five-byte prologue, one 32-bit
# input in rdi, an integer return in rax, and no output memory.
.globl _gfx_startup_stub_scalar
.p2align 4
_gfx_startup_stub_scalar:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x53
    movl %edi, %eax
    addl $7, %eax
    popq %rbx
    popq %rbp
    ret

# Vertex-buffer observation stubs. Six-byte safe prologue; the bodies return a
# shared header so the observer can assign a capture-local resource identity.
.globl _gfx_startup_stub_vb_create
.p2align 4
_gfx_startup_stub_vb_create:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57
    leaq _gfx_startup_stub_vb_header(%rip), %rax
    popq %r15
    popq %rbp
    ret

.globl _gfx_startup_stub_vb_update
.p2align 4
_gfx_startup_stub_vb_update:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57
    popq %r15
    popq %rbp
    ret

.globl _gfx_startup_stub_vb_bind
.p2align 4
_gfx_startup_stub_vb_bind:
    .byte 0x55, 0x48, 0x89, 0xe5, 0x41, 0x57
    popq %r15
    popq %rbp
    ret

.section __DATA,__data
.globl _gfx_startup_stub_vb_header
.align 4
_gfx_startup_stub_vb_header:
    .space 0x20, 0
