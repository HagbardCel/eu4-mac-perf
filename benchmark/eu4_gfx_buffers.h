#ifndef EU4_GFX_BUFFERS_H
#define EU4_GFX_BUFFERS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* nGLVBCount. Twelve bytes after gMasterContext. */
#define EU4_GFX_VB_COUNT_RVA 0x223857c

/* Registry mutations run on the thread bound at a successful _GfxInit.
   An unexpected thread stops before touching the table. */
void eu4_gfx_buffers_bind_thread(void);
/* Stops unless the caller is the thread bound at a successful _GfxInit. */
void eu4_gfx_require_thread(const char *symbol, const void *caller);

/* Nils MTLBuffers created by this backend token. Does not free headers.
   backend is the non-owning master+0x180 pointer. */
void eu4_gfx_release_vertex_buffers(void *backend, const void *caller);

void *eu4_gfx_create_vertex_buffer(void *ignored, const void *data, int32_t count, int32_t stride,
                                   int32_t usage);
void eu4_gfx_update_vertex_buffer(void *handle, void *header, const void *data, int32_t count,
                                  uint32_t offset, int32_t partial);
void eu4_gfx_destroy_vertex_buffer(void *header);

int eu4_gfx_vertex_records(void);
int eu4_gfx_vertex_gpus(void);
size_t eu4_gfx_vertex_buffer_length(void *header);
int eu4_gfx_vertex_buffer_read(void *header, void *dest, size_t n);
int eu4_gfx_vertex_buffer_shared(void *header);

void eu4_gfx_buffer_test_fail_metal(int on);
void eu4_gfx_buffer_test_fail_metal_throw(int on);
void eu4_gfx_buffer_test_fail_entry(int on);
void eu4_gfx_buffer_test_fail_insert(int on);
void eu4_gfx_buffer_test_fail_header(int on);

#ifdef __cplusplus
}
#endif

#endif
