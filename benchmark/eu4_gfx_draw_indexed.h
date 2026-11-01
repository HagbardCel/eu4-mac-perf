#ifndef EU4_GFX_DRAW_INDEXED_H
#define EU4_GFX_DRAW_INDEXED_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Offline argument-to-geometry adapter for _GfxDrawIndexed. Copies caller-supplied
   vertex records on the CPU. Does not own Metal resources, call drawIndexedPrimitives,
   replace the engine function, or reproduce an EU4 frame.
   Executable SHA-256 b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d.
   Symbol 0x15eaa77 through ud2 0x15eac94. rdi slot deref 0x15eaa91. esi>0 kept, else
   context+0x68 at 0x15eaa98 (jg at 0x15eaa96). edx unread in that span. ecx saved at
   0x15eaa8b; zero tail jumps to _glDrawElements stub 0x15eac6a; nonzero tail is
   jmpq *%rax at 0x15eac48 through ___glewDrawElementsBaseVertex. +0x70 null branches
   at 0x15eaaa5. +0x128 calls ___glewUniform4fvARB at 0x15eaaf4. +0x170 calls
   EnableVertexAttribArrays at 0x15eac08. Both tails pass mode from +0x8, type 0x1403,
   and index pointer 0. */

enum {
    EU4_GFX_DRAW_OK = 0,
    EU4_GFX_DRAW_NULL_CONTEXT = 1,
    EU4_GFX_DRAW_MISSING_PARENT = 2,
    EU4_GFX_DRAW_PENDING_UNIFORM = 3,
    EU4_GFX_DRAW_PENDING_ATTRIB = 4,
    EU4_GFX_DRAW_BAD_MODE = 5,
    EU4_GFX_DRAW_RANGE = 6,
    EU4_GFX_DRAW_LAYOUT = 7
};

/* Bytes required to read through context+0x170. */
#define EU4_GFX_DRAW_CONTEXT_BYTES 0x171

/* context_slot is rdi: one pointer to the context image. context_bytes is that
   image's length. Each resolved index copies exactly stride bytes. Success writes
   the selected vertex count and the written byte count. Failure writes no output. */
int eu4_gfx_draw_indexed_gather(const void *context_slot, int context_bytes, int count, int unused_edx,
                                int base_vertex, const void *vertices, int vertex_bytes, int stride,
                                int color_offset, int color_is_byte, const void *indices, int index_bytes,
                                void *out, int out_bytes, int *vertex_count_out, int *written_bytes_out,
                                int *as_strip_out);

#ifdef __cplusplus
}
#endif

#endif
