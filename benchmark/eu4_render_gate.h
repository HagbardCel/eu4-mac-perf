#ifndef EU4_RENDER_GATE_H
#define EU4_RENDER_GATE_H
#include <stdbool.h>
#include <stdint.h>

static inline bool eu4_render_due(uint64_t now,uint64_t *deadline,uint64_t period) {
    if(!period) { *deadline=0; return true; }
    if(!*deadline) { *deadline=now+period; return true; }
    if(now<*deadline) return false;
    uint64_t missed=(now-*deadline)/period;
    *deadline+=(missed+1)*period;
    return true;
}
#endif
