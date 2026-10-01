#ifndef EU4_RASTER_POLICY_H
#define EU4_RASTER_POLICY_H
#include <stdbool.h>
#include <stdint.h>
typedef struct { uintptr_t context; bool requested; } Eu4RasterContext;
/* The real GL state remains forced; restoration follows the last engine request. */
static inline bool eu4_raster_request(Eu4RasterContext *contexts,unsigned count,
                                     uintptr_t context,bool enabled) {
    for(unsigned i=0;i<count;i++) if(contexts[i].context==context) {
        contexts[i].requested=enabled; return true;
    }
    return false;
}
#endif
