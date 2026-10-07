#ifndef EU4_R0_CONTROL_H
#define EU4_R0_CONTROL_H

#include <stdint.h>

#define EU4_R0_CONTROL_MAGIC 0x5230304du /* 'R0M\0' */
#define EU4_R0_CONTROL_PAGE_SIZE 4096u

#define EU4_R0_MODE_PASSIVE 0u
#define EU4_R0_MODE_CENSUS 1u

/* Host writes this snapshot in one memcpy/msync so the probe never sees torn fields. */
typedef struct {
    uint32_t magic;
    uint32_t generation;
    uint32_t mode;
    uint32_t scenario_id;
} eu4_r0_control_snapshot_t;

#endif
