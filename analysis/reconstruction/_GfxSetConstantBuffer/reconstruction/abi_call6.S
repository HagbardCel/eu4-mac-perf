// RE1 ABI probe trampoline (x86-64 System V). Only built where an x86-64 binary can be
// assembled and executed; see the test for the gating.
//
// uint64_t abi_call6(void *fn, uint64_t v0, uint64_t v1, uint64_t v2, uint64_t v3,
//                    uint64_t v4, uint64_t v5);
//
// The ordinary calling convention already puts v0..v4 in RSI, RDX, RCX, R8, R9 and v5 on the
// stack, so this shifts each of them one register down - RDI, RSI, RDX, RCX, R8, R9 - which
// are exactly the positions the original `_GfxSetConstantBuffer` reads. It then calls `fn`
// with those registers and returns whatever fn left in RAX.
//
// Left-to-right order matters: each move reads a register a later move has not overwritten
// yet. `fn` is parked in R11, which is not part of the shifted set. The prologue keeps RSP
// 16-byte aligned at the call (16(%rsp) holds v5 after the 8-byte adjustment).

#if defined(__APPLE__)
#  define ABI_CALL6 _abi_call6
#else
#  define ABI_CALL6 abi_call6
#endif

    .text
    .globl ABI_CALL6
    .p2align 4
ABI_CALL6:
    subq $8, %rsp
    movq %rdi, %r11
    movq %rsi, %rdi
    movq %rdx, %rsi
    movq %rcx, %rdx
    movq %r8, %rcx
    movq %r9, %r8
    movq 16(%rsp), %r9
    call *%r11
    addq $8, %rsp
    ret
