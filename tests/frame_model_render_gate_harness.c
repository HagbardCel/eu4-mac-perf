#include "../benchmark/eu4_render_gate.h"
#include <stdio.h>

int main(void) {
    uint64_t deadline=0,period=100;
    if(!eu4_render_due(1000,&deadline,period) || deadline!=1100) return 2;
    if(eu4_render_due(1050,&deadline,period)) return 3;
    if(!eu4_render_due(1100,&deadline,period) || deadline!=1200) return 4;
    if(!eu4_render_due(1450,&deadline,period) || deadline!=1500) return 5;
    if(!eu4_render_due(1460,&deadline,0) || deadline!=0) return 6;
    deadline=1000;
    Eu4RenderDecision d=eu4_render_decide(1350,&deadline,100);
    if(!d.due || d.scheduled!=1000 || d.lateness!=350 || d.missed!=3 || deadline!=1400) return 7;
    d=eu4_render_decide(1351,&deadline,100);
    if(d.due || d.scheduled!=1400) return 8; /* no catch-up burst */
    puts("render gate passed early, on-time, missed-deadline, and reset cases");
    return 0;
}
