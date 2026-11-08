#include "eu4_gfx_observe_draw.h"

#include "eu4_gfx_observe_buffers.h"

#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* _GfxDraw at 0x15ea789. The body reads master+0x170 last, so 0x171 bytes
   cover every field the draw itself consumes. Mode at +8, effective count at
   +0x64, shader at +0x70. See analysis/metal-draw/d1b/gfx-draw-contract.md. */
enum {
    DRAW_MASTER_SIZE = EU4_GFX_OBSERVE_DRAW_MASTER_SIZE,
    DRAW_MODE_OFFSET = 0x08,
    DRAW_COUNT_OFFSET = 0x64,
    DRAW_SHADER_OFFSET = 0x70,
    DRAW_CONSTANT_COUNT_OFFSET = 0xC8,
    DRAW_CONSTANT_POINTER_OFFSET = 0xD0,
    DRAW_CONSTANT_MIN_VEC4 = 4,
    DRAW_MODE_TRIANGLE_STRIP = 5,
    DRAW_MIN_VERTICES = 3,
};

static _Atomic uint64_t g_expected_caller;
static _Atomic uint32_t g_nonmatch_samples;

typedef struct {
    int32_t primitive_enum;
    uint64_t primitive_call_id;
    int valid;
} draw_tls;

static _Thread_local draw_tls g_tls;

void eu4_gfx_observe_draw_configure(uint64_t target_caller) {
    atomic_store_explicit(&g_expected_caller, target_caller, memory_order_relaxed);
    atomic_store_explicit(&g_nonmatch_samples, 0, memory_order_relaxed);
}

void eu4_gfx_observe_draw_reset(void) {
    atomic_store_explicit(&g_expected_caller, 0, memory_order_relaxed);
    atomic_store_explicit(&g_nonmatch_samples, 0, memory_order_relaxed);
    memset(&g_tls, 0, sizeof(g_tls));
}

static int32_t load_i32(const uint8_t *bytes, size_t offset) {
    int32_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

static uint64_t load_u64(const uint8_t *bytes, size_t offset) {
    uint64_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

void eu4_gfx_observe_primitive_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_PRIMITIVE_SET);
    int32_t primitive = (int32_t)frame->rsi;
    eu4_gfx_observe_set_input(call, &primitive, sizeof(primitive));
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(meta, sizeof(meta), ",\"primitive_enum\":%d", primitive);
    eu4_gfx_observe_set_meta(call, meta);
    g_tls.primitive_enum = primitive;
    g_tls.primitive_call_id = call->call_id;
    g_tls.valid = 1;
    call->retain = 0;
    eu4_gfx_observe_count_primitive();
}

void eu4_gfx_observe_primitive_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record) {
    (void)frame;
    (void)call;
    (void)record;
}

void eu4_gfx_observe_draw_entry(eu4_gfx_capture_frame *frame,
                                eu4_gfx_observe_call *call) {
    const uintptr_t handle = (uintptr_t)frame->rdi;
    const int32_t raw_count = (int32_t)frame->rsi;
    const int32_t first = (int32_t)frame->rdx;

    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_DRAW);

    uint64_t master = 0;
    int master_ok = eu4_gfx_observe_read((const void *)handle, &master,
                                         sizeof(master)) &&
                    master != 0;
    uint8_t snapshot[DRAW_MASTER_SIZE];
    memset(snapshot, 0, sizeof(snapshot));
    int snapshot_ok =
        master_ok && eu4_gfx_observe_read_bytes((const void *)(uintptr_t)master,
                                                snapshot, sizeof(snapshot));
    int32_t mode = 0;
    int32_t master_count = 0;
    uint64_t shader = 0;
    if (snapshot_ok) {
        mode = load_i32(snapshot, DRAW_MODE_OFFSET);
        master_count = load_i32(snapshot, DRAW_COUNT_OFFSET);
        shader = load_u64(snapshot, DRAW_SHADER_OFFSET);
    }

    int32_t constant_vec4 = 0;
    uint64_t constant_header = 0;
    int constant_match = 0;
    uint64_t constant_call_id = 0;
    if (snapshot_ok) {
        constant_vec4 = load_i32(snapshot, DRAW_CONSTANT_COUNT_OFFSET);
        constant_header = load_u64(snapshot, DRAW_CONSTANT_POINTER_OFFSET);
        if (constant_header != 0 && constant_vec4 >= DRAW_CONSTANT_MIN_VEC4) {
            uint64_t data = 0;
            uint8_t bytes[64];
            if (eu4_gfx_observe_read(
                    (const void *)(uintptr_t)(constant_header + 8), &data,
                    sizeof(data)) &&
                data != 0 &&
                eu4_gfx_observe_read_bytes((const void *)(uintptr_t)data, bytes,
                                           sizeof(bytes))) {
                constant_match = eu4_gfx_observe_constant_matches(
                    (uintptr_t)constant_header, bytes, sizeof(bytes),
                    &constant_call_id);
            }
        }
    }

    const int32_t effective_count = raw_count > 0 ? raw_count : master_count;
    const char *count_source = raw_count > 0 ? "esi" : "master";
    if (!snapshot_ok && raw_count <= 0) {
        count_source = "unknown";
    }
    const uint64_t expected =
        atomic_load_explicit(&g_expected_caller, memory_order_relaxed);
    const int caller_match =
        expected == 0 ? 1 : (call->caller_known && call->caller_rva == expected);
    const int qualified = snapshot_ok && shader != 0 &&
                          mode == DRAW_MODE_TRIANGLE_STRIP &&
                          effective_count >= DRAW_MIN_VERTICES && caller_match;

    call->target_hit = qualified ? 1 : 0;
    call->target_selected =
        qualified && eu4_gfx_observe_claim_target() ? 1 : 0;
    if (qualified) {
        call->retain = call->target_selected;
    } else if (caller_match) {
        call->retain = 1;
    } else {
        uint32_t sample =
            atomic_fetch_add_explicit(&g_nonmatch_samples, 1,
                                      memory_order_relaxed) +
            1;
        call->retain =
            sample <= EU4_GFX_OBSERVE_NONMATCH_SAMPLE_CAP ? 1 : 0;
    }

    if (!snapshot_ok) {
        eu4_gfx_observe_fail_fields(call);
    }
    if (call->retain && snapshot_ok) {
        eu4_gfx_observe_set_payload(call, snapshot, sizeof(snapshot));
        if (!call->payload_ok) {
            eu4_gfx_observe_fail_fields(call);
        }
    }

    struct {
        uint64_t handle;
        int32_t raw_count;
        int32_t first;
        int32_t rcx;
        int32_t r8;
        int32_t r9;
    } input;
    memset(&input, 0, sizeof(input));
    input.handle = (uint64_t)handle;
    input.raw_count = raw_count;
    input.first = first;
    input.rcx = (int32_t)frame->rcx;
    input.r8 = (int32_t)frame->r8;
    input.r9 = (int32_t)frame->r9;
    eu4_gfx_observe_set_input(call, &input, sizeof(input));

    int32_t primitive = -1;
    uint64_t primitive_call_id = 0;
    if (g_tls.valid && g_tls.primitive_call_id < call->call_id) {
        primitive = g_tls.primitive_enum;
        primitive_call_id = g_tls.primitive_call_id;
    }
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(
        meta, sizeof(meta),
        ",\"mode\":%d,\"raw_count\":%d,\"first\":%d,\"effective_count\":%d,"
        "\"count_source\":\"%s\",\"shader_non_null\":%s,"
        "\"master_complete\":%s,\"caller_match\":%s,\"qualified\":%s,"
        "\"primitive_enum\":%d,\"primitive_call_id\":%llu,"
        "\"constant_slot0_vec4\":%d,\"constant_slot0_header\":%llu,"
        "\"constant_slot0_match\":%s,\"constant_slot0_call_id\":%llu",
        mode, raw_count, first, effective_count, count_source,
        shader != 0 ? "true" : "false", snapshot_ok ? "true" : "false",
        caller_match ? "true" : "false", qualified ? "true" : "false",
        primitive, (unsigned long long)primitive_call_id, constant_vec4,
        (unsigned long long)constant_header,
        constant_match ? "true" : "false",
        (unsigned long long)constant_call_id);
    eu4_gfx_observe_set_meta(call, meta);

    eu4_gfx_observe_count_draw(call->caller_rva, call->caller_known,
                               snapshot_ok ? mode : -1, effective_count);
}

void eu4_gfx_observe_draw_exit(eu4_gfx_capture_frame *frame,
                               eu4_gfx_observe_call *call,
                               eu4_gfx_observe_record *record) {
    (void)frame;
    (void)call;
    (void)record;
}
