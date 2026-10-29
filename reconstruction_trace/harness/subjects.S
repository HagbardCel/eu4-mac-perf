/*
 * Self-contained synthetic x86-64 subjects. Each is a leaf that touches only
 * memory reachable from its argument registers and returns in EAX/RAX.
 */
.section __TEXT,__text

/* rdi -> p: return p[8..12) when the dword at p is nonzero, else p[12..16). */
.globl _trace_subject_select
.p2align 4
_trace_subject_select:
    movl (%rdi), %eax
    testl %eax, %eax
    jz 1f
    movl 8(%rdi), %eax
    ret
1:
    movl 12(%rdi), %eax
    ret

/* rdi -> p, rsi -> q: overlapping writes to p, reads back, stores into q. */
.globl _trace_subject_overlap
.p2align 4
_trace_subject_overlap:
    movl $0x11223344, (%rdi)
    movl (%rdi), %ecx
    movw $0xbeef, 2(%rdi)
    movq (%rdi), %rax
    movl %ecx, (%rsi)
    ret

/* rdi -> a, a[0..8) -> b: b[4..8) = b[0..4) + a[8..12), also returned. */
.globl _trace_subject_chase
.p2align 4
_trace_subject_chase:
    movq (%rdi), %rdx
    movl (%rdx), %eax
    addl 8(%rdi), %eax
    movl %eax, 4(%rdx)
    ret

/* rdi -> p, rsi -> q: p[4..8) = 7, then return q[0..4). */
.globl _trace_subject_alias
.p2align 4
_trace_subject_alias:
    movl $7, 4(%rdi)
    movl (%rsi), %eax
    ret
