.section __DATA,__data
.globl _gfx_capture_native_entry_frame
.align 3
_gfx_capture_native_entry_frame:
    .quad 0
.globl _gfx_capture_native_exit_frame
.align 3
_gfx_capture_native_exit_frame:
    .quad 0
.globl _gfx_capture_native_entry_calls
.align 3
_gfx_capture_native_entry_calls:
    .quad 0
.globl _gfx_capture_native_exit_calls
.align 3
_gfx_capture_native_exit_calls:
    .quad 0
.globl _gfx_capture_native_entry_snapshot
.align 4
_gfx_capture_native_entry_snapshot:
    .space 648
.globl _gfx_capture_native_exit_snapshot
.align 4
_gfx_capture_native_exit_snapshot:
    .space 648
.globl _gfx_capture_native_poison_mxcsr
.align 4
_gfx_capture_native_poison_mxcsr:
    .long 0x9FC0

.section __TEXT,__text
.globl _eu4_gfx_set_constant_buffer_capture_entry
.p2align 4
_eu4_gfx_set_constant_buffer_capture_entry:
    movq %rdi, _gfx_capture_native_entry_frame(%rip)
    incq _gfx_capture_native_entry_calls(%rip)
    movq %rdi, %rsi
    leaq _gfx_capture_native_entry_snapshot(%rip), %rdi
    cld
    movl $81, %ecx
    rep movsq
    movabsq $0x1111111111111111, %rax
    movabsq $0x2222222222222222, %rcx
    movabsq $0x3333333333333333, %rdx
    movabsq $0x4444444444444444, %rsi
    movabsq $0x5555555555555555, %rdi
    movabsq $0x6666666666666666, %r8
    movabsq $0x7777777777777777, %r9
    movabsq $0x8888888888888888, %r10
    movabsq $0x9999999999999999, %r11
    pxor %xmm0, %xmm0
    pxor %xmm1, %xmm1
    pxor %xmm2, %xmm2
    pxor %xmm3, %xmm3
    pxor %xmm4, %xmm4
    pxor %xmm5, %xmm5
    pxor %xmm6, %xmm6
    pxor %xmm7, %xmm7
    pxor %xmm8, %xmm8
    pxor %xmm9, %xmm9
    pxor %xmm10, %xmm10
    pxor %xmm11, %xmm11
    pxor %xmm12, %xmm12
    pxor %xmm13, %xmm13
    pxor %xmm14, %xmm14
    pxor %xmm15, %xmm15
    ldmxcsr _gfx_capture_native_poison_mxcsr(%rip)
    stc
    ret

.globl _eu4_gfx_set_constant_buffer_capture_exit
.p2align 4
_eu4_gfx_set_constant_buffer_capture_exit:
    movq %rdi, _gfx_capture_native_exit_frame(%rip)
    incq _gfx_capture_native_exit_calls(%rip)
    movq %rdi, %rsi
    leaq _gfx_capture_native_exit_snapshot(%rip), %rdi
    cld
    movl $81, %ecx
    rep movsq
    movabsq $0xAAAAAAAAAAAAAAAA, %rax
    movabsq $0xBBBBBBBBBBBBBBBB, %rcx
    movabsq $0xCCCCCCCCCCCCCCCC, %rdx
    movabsq $0xDDDDDDDDDDDDDDDD, %rsi
    movabsq $0xEEEEEEEEEEEEEEEE, %rdi
    movabsq $0xFFFFFFFFFFFFFFFF, %r8
    movabsq $0x1010101010101010, %r9
    movabsq $0x2020202020202020, %r10
    movabsq $0x3030303030303030, %r11
    pxor %xmm0, %xmm0
    pxor %xmm1, %xmm1
    pxor %xmm2, %xmm2
    pxor %xmm3, %xmm3
    pxor %xmm4, %xmm4
    pxor %xmm5, %xmm5
    pxor %xmm6, %xmm6
    pxor %xmm7, %xmm7
    pxor %xmm8, %xmm8
    pxor %xmm9, %xmm9
    pxor %xmm10, %xmm10
    pxor %xmm11, %xmm11
    pxor %xmm12, %xmm12
    pxor %xmm13, %xmm13
    pxor %xmm14, %xmm14
    pxor %xmm15, %xmm15
    ldmxcsr _gfx_capture_native_poison_mxcsr(%rip)
    stc
    ret
