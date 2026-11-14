#ifndef EU4_METAL_EVENTS_H
#define EU4_METAL_EVENTS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void eu4_metal_events_set_dir(const char *dir);
void eu4_metal_events_set_image(uintptr_t base, uintptr_t end);
void eu4_metal_events_add_executable(uintptr_t start, uintptr_t end);

/* Temp sibling, fflush, fsync, then rename. An existing final file is left as it is. */
int eu4_metal_events_write(const char *name, const char *body);
/* Appends one record to a streaming JSONL file (unlike write, which keeps the
   first document and refuses to overwrite). */
int eu4_metal_events_append(const char *name, const char *body);

/* First terminating record wins. Caller is the return address to report. */
void eu4_metal_events_stop(const char *phase, const char *symbol, const char *reason,
                           const void *caller);

/* First successful create. 0 means written or already present. Never exits. */
int eu4_metal_events_create(const void *caller);

/* First successful index-buffer create. resource is the MTLBuffer pointer.
   0 means written or already present. Never exits. */
int eu4_metal_events_index_create(const void *caller, uintptr_t resource, uint64_t bytes,
                                  int32_t count);

/* Page-sized reads. *got is the valid prefix. A short read is not a full image. */
int eu4_metal_events_read(const void *src, void *dst, int want, int *got);

/* Caller image, pid, and thread. rva is set only when the address is inside the image. */
void eu4_metal_events_describe(const void *caller, char *image, size_t cap, uint64_t *rva,
                               int *inside, uint64_t *thread, int *pid);

#ifdef __cplusplus
}
#endif

#endif
