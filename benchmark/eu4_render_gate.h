#ifndef EU4_RENDER_GATE_H
#define EU4_RENDER_GATE_H
#include <stdbool.h>
#include <stdint.h>
typedef struct { bool due; uint64_t scheduled,lateness,missed; } Eu4RenderDecision;
static inline Eu4RenderDecision eu4_render_decide(uint64_t now,uint64_t *deadline,uint64_t period) {
    if(!period) { *deadline=0; return (Eu4RenderDecision){.due=true,.scheduled=now}; }
    if(!*deadline) { *deadline=now+period; return (Eu4RenderDecision){.due=true,.scheduled=now}; }
    if(now<*deadline) return (Eu4RenderDecision){.scheduled=*deadline};
    Eu4RenderDecision d={.due=true,.scheduled=*deadline,.lateness=now-*deadline,
                         .missed=(now-*deadline)/period};
    *deadline+=(d.missed+1)*period;
    return d;
}
static inline bool eu4_render_due(uint64_t now,uint64_t *deadline,uint64_t period) {
    return eu4_render_decide(now,deadline,period).due;
}
#endif
