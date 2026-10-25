#ifndef EU4_GFX_SET_CONSTANT_BUFFER_CAPTURE_H
#define EU4_GFX_SET_CONSTANT_BUFFER_CAPTURE_H

#include "eu4_gfx_capture_detour.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define EU4_GFX_CAPTURE_HANDLE_LENGTH 8u
#define EU4_GFX_CAPTURE_OBSERVATION_START 200u
#define EU4_GFX_CAPTURE_OBSERVATION_LENGTH 97u
#define EU4_GFX_CAPTURE_CANARY_BEFORE_START 184u
#define EU4_GFX_CAPTURE_CANARY_AFTER_START 297u
#define EU4_GFX_CAPTURE_CANARY_LENGTH 16u
#define EU4_GFX_CAPTURE_BUFFER_LENGTH 4u
#define EU4_GFX_CAPTURE_EXTENT_START EU4_GFX_CAPTURE_CANARY_BEFORE_START
#define EU4_GFX_CAPTURE_EXTENT_LENGTH 129u
#define EU4_GFX_CAPTURE_SLOT_MIN 0u
#define EU4_GFX_CAPTURE_SLOT_MAX 6u

enum {
    EU4_GFX_CAPTURE_SLOT_IDLE = 0,
    EU4_GFX_CAPTURE_SLOT_ACTIVE,
    EU4_GFX_CAPTURE_SLOT_COMPLETE,
};

/* One working capture or one published ticket. The live backend stores this
 * record, so the observation and serialization code is shared with PR B. */
typedef struct {
    int state;
    unsigned depth;
    unsigned capture_depth;
    unsigned refusals;
    unsigned thread;
    unsigned long sequence;
    unsigned ticket;
    uint64_t rdi;
    uint64_t rsi;
    uint64_t rcx;
    uint64_t r9;
    uintptr_t handle_address;
    uint8_t handle[EU4_GFX_CAPTURE_HANDLE_LENGTH];
    uintptr_t ctx_address;
    uint8_t ctx[EU4_GFX_CAPTURE_OBSERVATION_LENGTH];
    uint8_t canary_before[EU4_GFX_CAPTURE_CANARY_LENGTH];
    uint8_t canary_after[EU4_GFX_CAPTURE_CANARY_LENGTH];
    int buffer_path;
    uintptr_t buffer_address;
    uint8_t buffer[EU4_GFX_CAPTURE_BUFFER_LENGTH];
    uint8_t post_handle[EU4_GFX_CAPTURE_HANDLE_LENGTH];
    uint8_t post_ctx[EU4_GFX_CAPTURE_OBSERVATION_LENGTH];
    uint8_t post_canary_before[EU4_GFX_CAPTURE_CANARY_LENGTH];
    uint8_t post_canary_after[EU4_GFX_CAPTURE_CANARY_LENGTH];
    uint8_t post_buffer[EU4_GFX_CAPTURE_BUFFER_LENGTH];
    uint64_t outcome;
    /* v2 screening. Zero on the v1 path. */
    int diversity;
    int buffer_snapshotted;
    int phase_b_failed;
    int snapshot_mismatch;
    int coverage_designated;
    int sig_index;
    int sample_kind; /* 0 first, 1 anchored verify, 2 unanchored reprobe */
    uint32_t screen_slot;
    uint32_t screen_selector;
    uint32_t screen_input;
    uint32_t reserved_mask;
    uint64_t bound_generation;
} eu4_gfx_capture_record;

typedef struct {
    int ok;
    uint32_t slot;
    uint32_t word_count;
    uint32_t input_value;
    uint8_t selector_buffer;
    uint8_t input_class; /* 0 absent, 1 zero, 2 nonzero */
    uint8_t dirty;
    uint8_t buffer_state; /* 0 absent, 1 unchanged, 2 changed */
    uint8_t handle_state; /* 0 unchanged, 1 changed */
    uint8_t alias; /* 0 absent, 1 handle, 2 ctx, 3 distinct */
    uint8_t pointer_role; /* 0 rsi, 1 other */
    uint8_t rax_class; /* 0 ctx, 1 other */
    uint8_t canary; /* 0 preserved, 1 changed */
} eu4_gfx_behavior;

typedef struct {
    const char *capture;
    const char *scenario_buffer;
    const char *scenario_explicit;
    const char *epoch;
    const char *callsite;
    bool synthetic;
} eu4_gfx_capture_metadata;

typedef enum {
    EU4_GFX_CAPTURE_REFUSE_BUSY = 1,
    EU4_GFX_CAPTURE_REFUSE_SLOT,
    EU4_GFX_CAPTURE_REFUSE_POINTER,
    EU4_GFX_CAPTURE_REFUSE_ADDRESS,
    EU4_GFX_CAPTURE_REFUSE_QUOTA,
    EU4_GFX_CAPTURE_REFUSE_THREAD_LIMIT,
} eu4_gfx_capture_refusal;

typedef struct {
    /* thread_claim registers outside the hook (constructor) or inside the
     * armed-epoch guard; thread_lookup never registers. */
    eu4_gfx_capture_record *(*thread_claim)(void);
    eu4_gfx_capture_record *(*thread_lookup)(void);
    bool (*begin)(void);
    void (*end)(void);
    int (*admit)(const eu4_gfx_capture_frame *frame);
    /* Diversity Phase A+B. NULL keeps the v1 admit path. */
    int (*select)(eu4_gfx_capture_record *record,
                  const eu4_gfx_capture_frame *frame);
    bool (*publish)(unsigned ticket, const eu4_gfx_capture_record *record);
    void (*incomplete)(unsigned ticket);
    void (*refuse)(eu4_gfx_capture_refusal reason);
    bool safe_read;
} eu4_gfx_capture_live_ops;

void eu4_gfx_set_constant_buffer_capture_thread_init(void);
void eu4_gfx_set_constant_buffer_capture_entry(eu4_gfx_capture_frame *frame);
void eu4_gfx_set_constant_buffer_capture_exit(eu4_gfx_capture_frame *frame);
bool eu4_gfx_set_constant_buffer_capture_flush(void);
unsigned eu4_gfx_set_constant_buffer_capture_refusals(void);
bool eu4_gfx_set_constant_buffer_capture_install(void *site);

/* Live sessions install a backend; an absent backend keeps PR B's TLS path. */
void eu4_gfx_set_constant_buffer_capture_set_live(
    const eu4_gfx_capture_live_ops *ops);

/* Shared FUNCTION-CAPTURE v1 serializer; the caller owns the FILE. */
bool eu4_gfx_set_constant_buffer_capture_write(
    FILE *file, const eu4_gfx_capture_record *record,
    const eu4_gfx_capture_metadata *metadata);

bool eu4_gfx_set_constant_buffer_capture_read(void *destination,
                                              const void *source,
                                              size_t length);

/* Native fixture.assess / policy.behavior on this record's bytes.
 * missing_text is the sorted comma-separated Python missing list. */
unsigned eu4_gfx_set_constant_buffer_capture_assess(
    const eu4_gfx_capture_record *record, eu4_gfx_behavior *behavior,
    char *missing_text, size_t missing_length);

#endif
