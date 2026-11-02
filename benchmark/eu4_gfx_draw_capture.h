#ifndef EU4_GFX_DRAW_CAPTURE_H
#define EU4_GFX_DRAW_CAPTURE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Entered by a jmp from the patched prologue, so the stack still holds the
   game return address. These are not C functions. */
void eu4_gfx_draw_entry(void);
void eu4_gfx_draw_indexed_entry(void);
void eu4_gfx_draw_indexed_instanced_entry(void);

/* which: 1 draw, 2 indexed, 3 indexed instanced. Layout matches the shim. */
typedef struct {
    uint32_t which;
    uint32_t pad;
    uint64_t rdi, rsi, rdx, rcx, r8, r9;
    uint8_t xmm[8][16];
    uint64_t retaddr;
    uint8_t stack[64];
} eu4_gfx_draw_raw;

/* Writes the receipt JSON. Does not exit. 0 means the buffer holds one record. */
int eu4_gfx_draw_receipt(const eu4_gfx_draw_raw *raw, char *out, int cap);

#ifdef __cplusplus
}
#endif

#endif
