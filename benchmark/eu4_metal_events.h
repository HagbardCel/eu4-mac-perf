#ifndef EU4_METAL_EVENTS_H
#define EU4_METAL_EVENTS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void eu4_metal_events_set_dir(const char *dir);
void eu4_metal_events_set_image(uintptr_t base, uintptr_t end);

/* Temp sibling, fflush, fsync, then rename. An existing final file is left as it is. */
int eu4_metal_events_write(const char *name, const char *body);

/* First terminating record wins. Caller is the return address to report. */
void eu4_metal_events_stop(const char *phase, const char *symbol, const char *reason,
                           const void *caller);

#ifdef __cplusplus
}
#endif

#endif
