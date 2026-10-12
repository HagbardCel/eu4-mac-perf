#ifndef EU4_R0_CONTROL_H
#define EU4_R0_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

#define EU4_R0_CONTROL_PAGE_SIZE 4096u
#define EU4_R0_CONTROL_VERSION 1u

#define EU4_R0_MODE_PASSIVE 0u
#define EU4_R0_MODE_CENSUS 1u
#define EU4_R0_MODE_CENSUS_CAPTURE 2u

#define EU4_R0_SCENARIO_MAX 65535u
#define EU4_R0_MODE_MAX 255u

typedef struct {
    uint32_t generation;
    uint32_t scenario_id;
    uint32_t mode;
    uint32_t version;
    bool valid;
} eu4_r0_control_snapshot_t;

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#include <stdatomic.h>
typedef struct {
    _Alignas(8) atomic_uint_fast64_t word;
    uint8_t reserved[EU4_R0_CONTROL_PAGE_SIZE - sizeof(atomic_uint_fast64_t)];
} eu4_r0_control_page_t;
#else
#error "C11 atomics required for R0 control page"
#endif

uint64_t eu4_r0_control_pack(uint32_t generation, uint32_t scenario_id, uint32_t mode, uint32_t version);

bool eu4_r0_control_unpack(uint64_t packed, eu4_r0_control_snapshot_t *out);

void eu4_r0_control_publish(void *page, uint32_t generation, uint32_t mode, uint32_t scenario_id);

bool eu4_r0_control_consume(const volatile eu4_r0_control_page_t *control, eu4_r0_control_snapshot_t *out);

bool eu4_r0_control_atomics_supported(void);

#endif
