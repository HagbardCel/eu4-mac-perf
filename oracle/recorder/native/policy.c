/* Implementation of the Oracle P4 capture policy descriptor. See policy.h. */
#include "policy.h"

static void put_u32be(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)((value >> 24) & 0xffu);
    out[1] = (uint8_t)((value >> 16) & 0xffu);
    out[2] = (uint8_t)((value >> 8) & 0xffu);
    out[3] = (uint8_t)(value & 0xffu);
}

uint32_t policy_rule_for(const recorder_policy *policy, uint32_t hook_id)
{
    uint32_t i;
    if (policy == 0) {
        return RECORDER_RULE_DEEP;
    }
    for (i = 0; i < policy->rule_count && i < POLICY_MAX_RULES; i++) {
        if (policy->rules[i].hook_id == hook_id) {
            return policy->rules[i].rule;
        }
    }
    return policy->default_rule;
}

uint32_t policy_canonical_size(const recorder_policy *policy)
{
    if (policy == 0) {
        return 0;
    }
    return 12u + 8u * policy->rule_count;
}

uint32_t policy_canonical(const recorder_policy *policy, uint8_t *out, uint32_t out_size)
{
    uint32_t order[POLICY_MAX_RULES];
    uint32_t need;
    uint32_t i;
    uint32_t j;

    if (policy == 0 || out == 0) {
        return 0;
    }
    need = policy_canonical_size(policy);
    if (out_size < need) {
        return 0;
    }

    /* Sort rule indices by hook id so the encoding is independent of declaration
     * order. Insertion sort: the table is tiny and this must be deterministic. */
    for (i = 0; i < policy->rule_count && i < POLICY_MAX_RULES; i++) {
        order[i] = i;
    }
    for (i = 1; i < policy->rule_count && i < POLICY_MAX_RULES; i++) {
        uint32_t key = order[i];
        j = i;
        while (j > 0 && policy->rules[order[j - 1u]].hook_id > policy->rules[key].hook_id) {
            order[j] = order[j - 1u];
            j -= 1u;
        }
        order[j] = key;
    }

    put_u32be(out + 0, policy->version);
    put_u32be(out + 4, policy->default_rule);
    put_u32be(out + 8, policy->rule_count);
    for (i = 0; i < policy->rule_count && i < POLICY_MAX_RULES; i++) {
        const policy_rule *rule = &policy->rules[order[i]];
        put_u32be(out + 12u + 8u * i, rule->hook_id);
        put_u32be(out + 16u + 8u * i, rule->rule);
    }
    return need;
}
