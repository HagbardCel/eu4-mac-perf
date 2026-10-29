#include "probe_subject.h"

TR0_SCOPE_ATTR int64_t tr0_scale(const int64_t *factor, int64_t value) {
    return value * *factor;
}

TR0_SCOPE_ATTR int64_t tr0_chase(struct tr0_node *head, int64_t *out) {
    int64_t value = head->next->next->value;
    if (value > 40) {
        value = tr0_scale(&head->value, value);
    }
    *out = value;
    return value;
}
