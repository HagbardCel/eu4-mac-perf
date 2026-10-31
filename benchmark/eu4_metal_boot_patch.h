#ifndef EU4_METAL_BOOT_PATCH_H
#define EU4_METAL_BOOT_PATCH_H

#include <stdbool.h>
#include <stdint.h>

/* One five-byte CALL rel32. The original callee is the target decoded from those bytes. */

typedef struct {
    void *original;
    bool used_relay;
} eu4_metal_boot_patch;

bool eu4_metal_boot_rel32_fits(const void *callsite, const void *dest);
bool eu4_metal_boot_encode_call(const void *callsite, const void *dest, uint8_t out[5]);
void eu4_metal_boot_encode_relay(void *dst, void *target);

bool eu4_metal_boot_install(void *callsite, const uint8_t expected[5], void *expected_original,
                            void *wrapper, eu4_metal_boot_patch *out);
bool eu4_metal_boot_restore(void);

#endif
