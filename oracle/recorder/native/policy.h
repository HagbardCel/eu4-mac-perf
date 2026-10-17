/* Oracle P4 capture policy: which hooks are deeply captured.
 *
 * The policy is a declared, versioned descriptor, not a hard-coded list inside
 * the recorder. It selects which intercepted hooks are captured in full and which
 * are counted only. Because two policies qualify the same behavior differently,
 * a record must name the policy it was captured under - so the descriptor has a
 * canonical byte encoding that the offline side hashes into a policy digest. The
 * encoding is canonical (rules sorted by hook id, explicit big-endian fields), so
 * the digest depends on the policy's meaning and not on how it was written down.
 *
 * The native side does not hash: it only emits the canonical bytes. Hashing is
 * offline (P4.5), off the hot path.
 */
#ifndef EU4_ORACLE_RECORDER_NATIVE_POLICY_H
#define EU4_ORACLE_RECORDER_NATIVE_POLICY_H

#include <stdint.h>

#define POLICY_MAX_RULES 16u

enum {
    RECORDER_RULE_COUNT_ONLY = 0, /* count the interception; capture no state */
    RECORDER_RULE_DEEP = 1        /* capture the observation in full */
};

typedef struct {
    uint32_t hook_id;
    uint32_t rule;
} policy_rule;

typedef struct {
    uint32_t version;
    uint32_t default_rule;
    uint32_t rule_count;
    policy_rule rules[POLICY_MAX_RULES];
} recorder_policy;

/* The rule that applies to a hook: the first declared override, else the default. */
uint32_t policy_rule_for(const recorder_policy *policy, uint32_t hook_id);

/* Write the canonical descriptor bytes to `out`. Returns the number of bytes
 * written, or 0 if `out_size` is too small. */
uint32_t policy_canonical(const recorder_policy *policy, uint8_t *out, uint32_t out_size);

/* The canonical size in bytes, independent of `out`. */
uint32_t policy_canonical_size(const recorder_policy *policy);

#endif /* EU4_ORACLE_RECORDER_NATIVE_POLICY_H */
