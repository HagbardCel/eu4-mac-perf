#ifndef EU4_R0_CONTROL_H
#define EU4_R0_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

#define EU4_R0_CONTROL_MAGIC 0x5230304du /* 'R0M\0' */
#define EU4_R0_CONTROL_PAGE_SIZE 4096u

#define EU4_R0_MODE_PASSIVE 0u
#define EU4_R0_MODE_CENSUS 1u

typedef struct {
    uint32_t magic;
    uint32_t generation;
    uint32_t mode;
    uint32_t scenario_id;
} eu4_r0_control_snapshot_t;

typedef struct {
    volatile uint32_t seq;
    uint32_t magic;
    uint32_t generation;
    uint32_t mode;
    uint32_t scenario_id;
} eu4_r0_control_page_t;

void eu4_r0_control_publish(void *page, uint32_t generation, uint32_t mode, uint32_t scenario_id);

bool eu4_r0_control_consume(const volatile eu4_r0_control_page_t *control,
                            eu4_r0_control_snapshot_t *out);

#endif
