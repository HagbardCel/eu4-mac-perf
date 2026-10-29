#ifndef TR0_PROBE_SUBJECT_H
#define TR0_PROBE_SUBJECT_H

#include <stdint.h>

/* Every subject function lives in this section; its bounds are the declared
 * instrumentation scope. */
#define TR0_SCOPE_SECTION "__TEXT,__tr0_scope,regular,pure_instructions"
#define TR0_SCOPE_ATTR __attribute__((noinline, used, section(TR0_SCOPE_SECTION)))

struct tr0_node {
    struct tr0_node *next;
    int64_t value;
};

int64_t tr0_scale(const int64_t *factor, int64_t value);
int64_t tr0_chase(struct tr0_node *head, int64_t *out);

#endif
