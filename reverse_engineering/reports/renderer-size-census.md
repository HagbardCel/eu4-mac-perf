# Renderer size census

Aggregate metrics from Ghidra static export (no pseudocode in this report).

## Executable coverage

- E (executable section bytes): 10000
- D (decoded instruction bytes): 5000
- F_internal: 17
- F_thunk: 6
- F_all (recovered internal+thunk): 23
- C_section (F_all/E): 0.0023
- C_decoded (F_all/D): 0.0046

## Subsystem sizes (confirmed + probable)

| Subsystem | Code bytes |
|-----------|------------|
| B0 | 14 |
| B1 | 5 |

## OpenGL coupling

{
  "internal_functions_direct_opengl": 1,
  "internal_functions_opengl_via_stub": 0,
  "unresolved_indirect_call_sites": 1
}

## Gfx draw-site reconciliation

{}
