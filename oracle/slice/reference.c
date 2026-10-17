/* Reference implementation for the Oracle P3 synthetic slice.
 *
 * This is the trusted baseline. It must pass both the visible and held-out
 * suites; that qualification is checked before any agent is invoked. It is
 * never placed in the agent workspace.
 */
#include "synth.h"

int synth_apply(struct synth_state *s, int mode, int arg) {
    switch (mode) {
    case 0:
        s->dirty = 1;
        synth_emit(SYNTH_FIELD_DIRTY, 1);
        s->value = arg;
        synth_emit(SYNTH_FIELD_VALUE, arg);
        return 0;
    case 1:
        s->counter += arg;
        synth_emit(SYNTH_FIELD_COUNTER, s->counter);
        return s->counter;
    case 2:
        s->value = arg;
        synth_emit(SYNTH_FIELD_VALUE, arg);
        return arg;
    default:
        return -1;
    }
}
