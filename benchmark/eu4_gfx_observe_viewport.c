#include "eu4_gfx_observe_viewport.h"

/* _GfxSetViewPort at 0x15ea4f8. rdi is the master, rsi points at the 24-byte
   rectangle. The original copies that image to master+0xac and tail-jumps to
   _glViewport. The exit hook runs after that GL call returns; the master state
   is already written. */
void eu4_gfx_observe_viewport_entry(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call) {
    eu4_gfx_observe_set_output_base(call, (void *)(uintptr_t)frame->rdi);
    eu4_gfx_observe_set_input(call, (const void *)(uintptr_t)frame->rsi, 24);
}

void eu4_gfx_observe_viewport_exit(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call,
                                   eu4_gfx_observe_record *record) {
    (void)frame;
    (void)record;
    if (call->output_base == NULL) {
        call->output_ok = 0;
        return;
    }
    eu4_gfx_observe_set_output(call,
                               (const uint8_t *)call->output_base + 0xac, 24);
}
