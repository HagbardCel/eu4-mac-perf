#ifndef EU4_GFX_OBSERVE_H
#define EU4_GFX_OBSERVE_H

#include "eu4_gfx_capture_detour.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EU4_GFX_OBSERVE_MAX_SPECS 8u
#define EU4_GFX_OBSERVE_RECORD_CAP 1024u
#define EU4_GFX_OBSERVE_BLOB_SIZE 64u
#define EU4_GFX_OBSERVE_DEFAULT_MAX_EVENTS 256u
#define EU4_GFX_OBSERVE_DEFAULT_DURATION_MS 10000u

#define EU4_GFX_OBSERVE_RELOC_UNSAFE 0
#define EU4_GFX_OBSERVE_RELOC_SAFE 1

typedef struct eu4_gfx_observe_call {
    uint64_t call_id;
    uint64_t entry_time_ns;
    uint64_t thread_id;
    uint64_t caller_rva;
    int caller_known;
    void *output_base;
    int input_ok;
    int output_ok;
    int return_valid;
    int64_t return_value;
    int overlap;
    uint8_t input[EU4_GFX_OBSERVE_BLOB_SIZE];
    size_t input_size;
    uint8_t output[EU4_GFX_OBSERVE_BLOB_SIZE];
    size_t output_size;
} eu4_gfx_observe_call;

typedef struct eu4_gfx_observe_record {
    uint64_t call_id;
    uint64_t completion_seq;
    uint32_t slot;
    uint64_t thread_id;
    uint64_t caller_rva;
    int caller_known;
    int overlap;
    int fields_ok;
    int return_valid;
    int64_t return_value;
    int output_base_non_null;
    uint8_t input[EU4_GFX_OBSERVE_BLOB_SIZE];
    size_t input_size;
    uint8_t output[EU4_GFX_OBSERVE_BLOB_SIZE];
    size_t output_size;
} eu4_gfx_observe_record;

typedef struct {
    const char *symbol;
    uint64_t rva;
    const uint8_t *prefix;
    size_t length;
    int relocation;
    void (*on_entry)(eu4_gfx_capture_frame *frame, eu4_gfx_observe_call *call);
    void (*on_exit)(eu4_gfx_capture_frame *frame, eu4_gfx_observe_call *call,
                    eu4_gfx_observe_record *record);
} eu4_gfx_observe_spec;

typedef struct {
    uint32_t state; /* 0 armed, 1 draining, 2 sealed */
    uint32_t enabled;
    uint32_t events;
    uint32_t capacity;
    uint32_t max_events;
    uint32_t duration_ms;
    uint32_t overlaps;
    uint32_t refused;
    uint32_t unmatched;
    uint32_t unavailable;
    uint32_t overflow;
    uint32_t in_flight;
    int integrity_ok;
    int records_copied;
    int init_seen;
    uint64_t init_call_id;
    uint64_t first_event_ns;
    uint64_t last_event_ns;
    char reason[64];
    const eu4_gfx_observe_record *records;
    uint32_t record_count;
} eu4_gfx_observe_snapshot;

bool eu4_gfx_observe_configure_from_env(void);
bool eu4_gfx_observe_install(uintptr_t image_base, uintptr_t text_begin,
                             uintptr_t text_end);
bool eu4_gfx_observe_install_specs(const eu4_gfx_observe_spec *specs, size_t count,
                                   uintptr_t image_base, uintptr_t text_begin,
                                   uintptr_t text_end);
void eu4_gfx_observe_poll(void);
bool eu4_gfx_observe_should_stop(void);
bool eu4_gfx_observe_enabled(void);
void eu4_gfx_observe_mark_init(uint64_t call_id);
uint32_t eu4_gfx_observe_in_flight(void);
void eu4_gfx_observe_copy_snapshot(eu4_gfx_observe_snapshot *out);
bool eu4_gfx_observe_finalize(const char *run_dir, const char *session,
                              const char *executable_sha, int pid,
                              uintptr_t image_base);

/* Callback helpers. */
bool eu4_gfx_observe_read(const void *src, void *dest, size_t n);
void eu4_gfx_observe_set_output_base(eu4_gfx_observe_call *call, void *base);
void eu4_gfx_observe_set_input(eu4_gfx_observe_call *call, const void *src,
                               size_t n);
void eu4_gfx_observe_set_output(eu4_gfx_observe_call *call, const void *src,
                                size_t n);
void eu4_gfx_observe_set_return(eu4_gfx_observe_call *call, int64_t value);

/* Test hooks. */
void eu4_gfx_observe_test_reset(void);
void eu4_gfx_observe_test_force_unpaired(void);
void eu4_gfx_observe_test_fail_next_output_read(void);
void eu4_gfx_observe_test_force_install_failure(int after_sites);
void eu4_gfx_observe_test_set_init_seen(int on);
void eu4_gfx_observe_test_set_limits(uint32_t max_events, uint32_t duration_ms);
void eu4_gfx_observe_test_force_overlap(void);
uint32_t eu4_gfx_observe_test_events(void);

/* Gateway stubs emitted by eu4_gfx_observe_gateway.S. */
extern void eu4_gfx_observe_gateway_0(void);
extern void eu4_gfx_observe_gateway_1(void);
extern void eu4_gfx_observe_gateway_2(void);
extern void eu4_gfx_observe_gateway_3(void);
extern void eu4_gfx_observe_gateway_4(void);
extern void eu4_gfx_observe_gateway_5(void);
extern void eu4_gfx_observe_gateway_6(void);
extern void eu4_gfx_observe_gateway_7(void);
typedef void (*eu4_gfx_observe_hook)(eu4_gfx_capture_frame *frame,
                                     uint64_t slot);
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_0;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_1;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_2;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_3;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_4;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_5;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_6;
extern eu4_gfx_observe_hook eu4_gfx_observe_entry_7;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_0;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_1;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_2;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_3;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_4;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_5;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_6;
extern eu4_gfx_observe_hook eu4_gfx_observe_exit_7;
extern void *eu4_gfx_observe_trampoline_0;
extern void *eu4_gfx_observe_trampoline_1;
extern void *eu4_gfx_observe_trampoline_2;
extern void *eu4_gfx_observe_trampoline_3;
extern void *eu4_gfx_observe_trampoline_4;
extern void *eu4_gfx_observe_trampoline_5;
extern void *eu4_gfx_observe_trampoline_6;
extern void *eu4_gfx_observe_trampoline_7;

#endif
