#include "eu4_submission_border_loop_head.h"

#include <immintrin.h>
#include <stdint.h>

static unsigned char g_fx_save_region[512] __attribute__((aligned(64)));

void eu4_border_loop_head_gateway_from_regs(uint8_t *index_cursor, uint8_t visibility_mask) {
    (void)index_cursor;
    (void)visibility_mask;
    _fxsave64(g_fx_save_region);
    /* Live EU IV path: decode walk slice from registers/stack; stub leaves classification to test_invoke. */
    _fxrstor64(g_fx_save_region);
}

