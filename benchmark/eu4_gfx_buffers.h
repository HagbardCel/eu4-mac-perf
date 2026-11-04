#ifndef EU4_GFX_BUFFERS_H
#define EU4_GFX_BUFFERS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* nGLVBCount. Twelve bytes after gMasterContext. */
#define EU4_GFX_VB_COUNT_RVA 0x223857c
/* bOutputError. The null-header path in _GfxSetVertexBuffers. */
#define EU4_GFX_VB_ERROR_RVA 0x2238580
/* Pointers [+0x130, +0x150), offsets [+0x150, +0x160), third dwords [+0x160, +0x170). */
#define EU4_GFX_VERTEX_SLOTS 4

/* nGLIBCount. Four bytes after nGLVBCount. */
#define EU4_GFX_IB_COUNT_RVA 0x2238584
/* Recycled 8-byte index headers. The top is the number of filled slots. */
#define EU4_GFX_IB_CACHE_TOP_RVA 0x2238588
#define EU4_GFX_IB_CACHE_RVA 0x2238590
#define EU4_GFX_IB_CACHE_SLOTS 128

/* Registry mutations run on the thread bound at a successful _GfxInit.
   An unexpected thread stops before touching the table. */
void eu4_gfx_buffers_bind_thread(void);
/* 1 when the caller is the thread bound at a successful _GfxInit. */
int eu4_gfx_buffers_on_thread(void);
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

/* Index buffers. A second registry keyed by the 8-byte engine header: +0 count,
   +4 GL element-array name. Logical bytes are twice the 16-bit index count. */
void *eu4_gfx_create_index_buffer(void *ignored, const void *data, int32_t count);
void eu4_gfx_update_index_buffer(void *ignored, void *header, const void *data, int32_t count);
void eu4_gfx_destroy_index_buffer(void *header);
void eu4_gfx_set_index_buffer(void *handle, void *header);
int eu4_gfx_index_records(void);
int eu4_gfx_index_gpus(void);
size_t eu4_gfx_index_buffer_length(void *header);
int eu4_gfx_index_buffer_read(void *header, void *dest, size_t n);
int eu4_gfx_index_buffer_shared(void *header);
void *eu4_gfx_current_index_header(void);
int eu4_gfx_current_index_read(void *dest, size_t n);

/* Vertex selection. Headers only; the MTLBuffer is resolved from the registry. */
void eu4_gfx_set_vertex_buffers(void *handle, int32_t count, void **headers, int32_t start,
                                const int32_t *offsets, const int32_t *words);
void *eu4_gfx_vertex_slot_header(int slot);
int eu4_gfx_vertex_slot_read(int slot, void *dest, size_t n);
/* Borrowed MTLBuffer for a later encoder. Each call resolves the slot header
   through the vertex registry and returns the current buffer. The selection
   does not retain a copy. NULL when the slot, entry, or buffer is absent. */
void *eu4_gfx_vertex_slot_buffer(int slot);
int eu4_gfx_disabled_vertex_attrib_count(void);
int eu4_gfx_disabled_vertex_attrib(int index, int32_t *out);

#ifdef __cplusplus
}
#endif

#endif
