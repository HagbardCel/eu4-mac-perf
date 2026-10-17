// Minimal x86-64 Mach-O for ExportCallsites fixture (macOS).
    .globl _start
    .text
_start:
    call _target_direct
    call *%rax
    call *(%rdi)
    ret

_target_direct:
    ret
