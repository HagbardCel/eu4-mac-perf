/* Defective candidate for the Oracle P3 synthetic slice.
 *
 * Supplied to the reconstruction agent so it can repair the fault from Oracle
 * diagnostics alone. The defect is a missing post-state write: in mode 0 the
 * `value` field is never updated, so both the raw post-state bytes and the
 * ordered effect transcript diverge from the reference.
 *
 * A narrow repair that only emits the missing effect (without writing the
 * field) still fails the held-out suite, which is the point of the exercise.
 */
#include "synth.h"

int synth_apply(struct synth_state *s, int mode, int arg) {
    switch (mode) {
    case 0:
        s->dirty = 1;
        synth_emit(SYNTH_FIELD_DIRTY, 1);
        /* BUG: s->value = arg; and its effect are missing. */
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
