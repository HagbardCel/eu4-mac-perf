/* Trusted harness driver for the Oracle P3 synthetic slice.
 *
 * Compiled together with a candidate that implements synth_apply(). It
 * initializes the pre-state from argv, invokes the candidate exactly once, and
 * prints a line protocol on stdout that the Python harness parses into an
 * observation envelope:
 *
 *   RET <8 hex digits>        returned int, two's-complement, little-endian bits
 *   MEM <24 hex digits>       raw post-state bytes of struct synth_state
 *   EFF <field> <value>       one line per emitted ordered effect
 *   END <effect count>        terminator; its absence marks an incomplete run
 *
 * The driver never decides equality; it only reports what happened.
 */
#include <stdio.h>
#include <stdlib.h>
#include "synth.h"

static int g_effect_count = 0;

void synth_emit(int field, int value) {
    printf("EFF %d %d\n", field, value);
    g_effect_count++;
}

static int parse_int(const char *text, int *out) {
    char *end = NULL;
    long value = strtol(text, &end, 10);
    if (end == text || (end != NULL && *end != '\0')) {
        return -1;
    }
    *out = (int)value;
    return 0;
}

int main(int argc, char **argv) {
    if (sizeof(struct synth_state) != SYNTH_STATE_BYTES) {
        fprintf(stderr, "state layout mismatch: %lu != %d\n",
                (unsigned long)sizeof(struct synth_state), SYNTH_STATE_BYTES);
        return 90;
    }
    if (argc != 6) {
        fprintf(stderr, "usage: %s MODE ARG DIRTY VALUE COUNTER\n", argv[0]);
        return 91;
    }
    int values[5];
    for (int i = 0; i < 5; i++) {
        if (parse_int(argv[i + 1], &values[i]) != 0) {
            fprintf(stderr, "not an int: %s\n", argv[i + 1]);
            return 92;
        }
    }

    struct synth_state state;
    state.dirty = values[2];
    state.value = values[3];
    state.counter = values[4];

    int result = synth_apply(&state, values[0], values[1]);

    printf("RET %08x\n", (unsigned int)result);
    const unsigned char *raw = (const unsigned char *)&state;
    printf("MEM ");
    for (unsigned long i = 0; i < (unsigned long)sizeof(state); i++) {
        printf("%02x", raw[i]);
    }
    printf("\n");
    printf("END %d\n", g_effect_count);
    return 0;
}
