/* Trusted public interface for the Oracle P3 synthetic slice.
 *
 * This header is NOT part of the agent workspace. The trusted harness copies it
 * into a private build directory, so a candidate cannot alter the ABI by
 * shipping its own copy. The interface is delivered to the agent through the
 * read-only `oracle_inspect` tool.
 *
 * The slice models a pinned graphics setter with two observable domains:
 * a raw post-state byte span and an ordered effect transcript.
 */
#ifndef EU4_ORACLE_SLICE_SYNTH_H
#define EU4_ORACLE_SLICE_SYNTH_H

#define SYNTH_STATE_BYTES 12

struct synth_state {
    int dirty;   /* byte offset 0  */
    int value;   /* byte offset 4  */
    int counter; /* byte offset 8  */
};

/* Field identifiers used in ordered effect records. */
enum synth_field {
    SYNTH_FIELD_DIRTY = 1,
    SYNTH_FIELD_VALUE = 2,
    SYNTH_FIELD_COUNTER = 3
};

/* Trusted effect sink, implemented by the harness driver. Records an ordered
 * effect event every time the candidate performs an observable write. */
void synth_emit(int field, int value);

/* Candidate-implemented function under reconstruction. */
int synth_apply(struct synth_state *s, int mode, int arg);

#endif /* EU4_ORACLE_SLICE_SYNTH_H */
