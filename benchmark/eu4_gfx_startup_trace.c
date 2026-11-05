#include "eu4_gfx_startup_trace.h"

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

enum {
    FINALIZE_IDLE = 0,
    FINALIZE_WRITING = 1,
    FINALIZE_DONE = 2,
};

typedef struct {
    uint64_t call_id;
    uint32_t site;
    void *settings;
    void *success;
    int32_t sample_count;
    int admitted;
} tls_frame;

typedef struct {
    tls_frame stack[EU4_GFX_STARTUP_TLS_DEPTH];
    unsigned depth;
} tls_stack;

static _Atomic uint32_t g_state = EU4_GFX_STARTUP_ARMED;
static _Atomic uint64_t g_next_call = 1;
static _Atomic uint32_t g_in_flight = 0;
static _Atomic uint32_t g_overflow = 0;
static _Atomic uint32_t g_unmatched = 0;
static _Atomic uint32_t g_refused = 0;
static _Atomic uint32_t g_unavailable = 0;
static _Atomic uint32_t g_settings_calls = 0;
static _Atomic uint32_t g_init_calls = 0;
static _Atomic uint32_t g_qualifying = 0;
static _Atomic uint32_t g_event_count = 0;
static eu4_gfx_startup_event g_events[EU4_GFX_STARTUP_EVENT_CAP];
static _Thread_local tls_stack g_tls;
static _Thread_local unsigned g_refuse_depth;
static int g_force_unsafe;
static int g_force_tls_full;
static _Atomic int g_pause_after_admit;
static _Atomic int g_admit_paused;
static _Atomic int g_admit_release;
static _Atomic unsigned g_fail_writes;
static _Atomic uint32_t g_finalize_phase = FINALIZE_IDLE;
static _Atomic int g_finalize_ok;
static uintptr_t g_image_base;
static uintptr_t g_text_begin;
static uintptr_t g_text_end;

static bool safe_read(void *dest, const void *src, size_t n) {
    if (dest == NULL || src == NULL || n == 0) {
        return false;
    }
    if (g_force_unsafe) {
        return false;
    }
    uintptr_t address = (uintptr_t)src;
    if (address > UINTPTR_MAX - (n - 1)) {
        return false;
    }
    mach_vm_size_t got = 0;
    kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), (mach_vm_address_t)address, (mach_vm_size_t)n,
        (mach_vm_address_t)(uintptr_t)dest, &got);
    return result == KERN_SUCCESS && got == n;
}

static uint64_t thread_id(void) {
    uint64_t value = 0;
    (void)pthread_threadid_np(NULL, &value);
    return value;
}

static void maybe_caller(eu4_gfx_startup_event *event, eu4_gfx_capture_frame *frame) {
    uint64_t ret = 0;
    const uint8_t *above = (const uint8_t *)frame + sizeof(*frame);
    if (!safe_read(&ret, above, sizeof(ret))) {
        event->caller_known = 0;
        event->caller_rva = 0;
        return;
    }
    if (g_image_base != 0 && ret >= g_text_begin && ret < g_text_end) {
        event->caller_known = 1;
        event->caller_rva = ret - g_image_base;
    } else {
        event->caller_known = 0;
        event->caller_rva = 0;
    }
}

static void try_seal(void) {
    if (atomic_load_explicit(&g_state, memory_order_seq_cst) !=
        EU4_GFX_STARTUP_DRAINING) {
        return;
    }
    if (atomic_load_explicit(&g_in_flight, memory_order_seq_cst) == 0) {
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_SEALED, memory_order_seq_cst);
    }
}

static bool commit_event(eu4_gfx_startup_event *event) {
    uint32_t index =
        atomic_fetch_add_explicit(&g_event_count, 1, memory_order_seq_cst);
    if (index >= EU4_GFX_STARTUP_EVENT_CAP) {
        atomic_fetch_add_explicit(&g_overflow, 1, memory_order_relaxed);
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_seq_cst);
        return false;
    }
    event->seq = (uint64_t)index + 1;
    g_events[index] = *event;
    return true;
}

static bool push_tls(tls_frame frame) {
    if (g_force_tls_full || g_tls.depth >= EU4_GFX_STARTUP_TLS_DEPTH) {
        atomic_fetch_add_explicit(&g_overflow, 1, memory_order_relaxed);
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_seq_cst);
        return false;
    }
    g_tls.stack[g_tls.depth++] = frame;
    return true;
}

static bool pop_tls(uint32_t site, tls_frame *out) {
    if (g_tls.depth == 0) {
        atomic_fetch_add_explicit(&g_unmatched, 1, memory_order_relaxed);
        return false;
    }
    tls_frame top = g_tls.stack[g_tls.depth - 1];
    if (top.site != site || !top.admitted) {
        atomic_fetch_add_explicit(&g_unmatched, 1, memory_order_relaxed);
        return false;
    }
    g_tls.depth -= 1;
    *out = top;
    return true;
}

static void refuse_entry(void) {
    g_refuse_depth += 1;
    atomic_fetch_add_explicit(&g_refused, 1, memory_order_relaxed);
}

static void withdraw_admission(void) {
    atomic_fetch_sub_explicit(&g_in_flight, 1, memory_order_seq_cst);
    refuse_entry();
    try_seal();
}

static void maybe_pause_after_admit(void) {
    if (!atomic_load_explicit(&g_pause_after_admit, memory_order_acquire)) {
        return;
    }
    atomic_store_explicit(&g_admit_paused, 1, memory_order_release);
    while (!atomic_load_explicit(&g_admit_release, memory_order_acquire)) {
        usleep(1000);
    }
}

static int read_settings_blob(eu4_gfx_startup_event *event, void *settings,
                              int32_t requested) {
    event->sample_count = requested;
    event->sample_captured = 0;
    if (settings == NULL) {
        event->settings_present = 0;
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    event->settings_present = 1;
    if (requested < 0 || requested > (int32_t)EU4_GFX_STARTUP_SAMPLE_CAP) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    uint8_t header[0x28];
    if (!safe_read(header, settings, sizeof(header))) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    event->settings_byte10 = header[0x10];
    memcpy(&event->anisotropy, header + 0x14, 4);
    memcpy(&event->span, header + 0x18, 8);
    memcpy(&event->constant, header + 0x20, 4);
    void *array = NULL;
    memcpy(&array, header + 8, sizeof(array));
    event->sample_captured = requested;
    if (requested == 0) {
        event->samples_ok = 1;
        return 1;
    }
    if (array == NULL) {
        event->sample_captured = 0;
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    uint8_t records[EU4_GFX_STARTUP_SAMPLE_CAP * EU4_GFX_STARTUP_SAMPLE_SIZE];
    if (!safe_read(records, array, (size_t)requested * EU4_GFX_STARTUP_SAMPLE_SIZE)) {
        event->sample_captured = 0;
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    for (int32_t i = 0; i < requested; i++) {
        memcpy(&event->sample_values[i],
               records + (size_t)i * EU4_GFX_STARTUP_SAMPLE_SIZE, 4);
        event->sample_flags[i] =
            records[(size_t)i * EU4_GFX_STARTUP_SAMPLE_SIZE + 4];
    }
    event->samples_ok = 1;
    return 1;
}

static int read_master(eu4_gfx_startup_event *event, void *master) {
    if (master == NULL) {
        event->master_non_null = 0;
        return 0;
    }
    event->master_non_null = 1;
    uint8_t body[EU4_GFX_STARTUP_MASTER_SIZE];
    if (!safe_read(body, master, sizeof(body))) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    memcpy(&event->primitive, body + 8, 4);
    memcpy(&event->width, body + 0xa0, 4);
    memcpy(&event->height, body + 0xa4, 4);
    memcpy(&event->source, body + 0xa8, 4);
    memcpy(event->viewport, body + 0xac, sizeof(event->viewport));
    event->dirty = body[0x170];
    event->lod = body[0x18e];
    memcpy(&event->max_texture, body + 0x188, 4);
    void *blend = NULL, *depth = NULL, *raster = NULL, *ctx = NULL;
    memcpy(&blend, body + 0x80, sizeof(blend));
    memcpy(&depth, body + 0x88, sizeof(depth));
    memcpy(&raster, body + 0x90, sizeof(raster));
    memcpy(&ctx, body + 0x180, sizeof(ctx));
    event->blend_present = blend != NULL;
    event->depth_present = depth != NULL;
    event->raster_present = raster != NULL;
    event->gl_context_present = ctx != NULL;
    event->master_ok = 1;
    return 1;
}

static int begin_admit(void) {
    atomic_fetch_add_explicit(&g_in_flight, 1, memory_order_seq_cst);
    uint32_t state = atomic_load_explicit(&g_state, memory_order_seq_cst);
    if (state != EU4_GFX_STARTUP_ARMED) {
        withdraw_admission();
        return 0;
    }
    maybe_pause_after_admit();
    return 1;
}

static void on_settings_entry(eu4_gfx_capture_frame *frame) {
    if (!begin_admit()) {
        return;
    }
    eu4_gfx_startup_event event;
    memset(&event, 0, sizeof(event));
    event.site = EU4_GFX_STARTUP_SITE_SETTINGS;
    event.phase = 0;
    event.thread_id = thread_id();
    event.admitted = 1;
    event.call_id = atomic_fetch_add_explicit(&g_next_call, 1, memory_order_relaxed);
    event.display = (int32_t)frame->rdi;
    event.windowed = (int32_t)frame->rsi;
    int32_t requested = (int32_t)frame->rdx;
    void *settings = (void *)(uintptr_t)frame->rcx;
    tls_frame inv = {
        .call_id = event.call_id,
        .site = EU4_GFX_STARTUP_SITE_SETTINGS,
        .settings = settings,
        .success = NULL,
        .sample_count = requested,
        .admitted = 1,
    };
    if (!push_tls(inv)) {
        withdraw_admission();
        return;
    }
    maybe_caller(&event, frame);
    event.fields_ok = read_settings_blob(&event, settings, requested);
    atomic_fetch_add_explicit(&g_settings_calls, 1, memory_order_relaxed);
    (void)commit_event(&event);
}

static void on_settings_exit(eu4_gfx_capture_frame *frame) {
    if (g_refuse_depth > 0) {
        g_refuse_depth -= 1;
        return;
    }
    tls_frame inv;
    if (!pop_tls(EU4_GFX_STARTUP_SITE_SETTINGS, &inv)) {
        return;
    }
    eu4_gfx_startup_event event;
    memset(&event, 0, sizeof(event));
    event.site = EU4_GFX_STARTUP_SITE_SETTINGS;
    event.phase = 1;
    event.thread_id = thread_id();
    event.admitted = 1;
    event.call_id = inv.call_id;
    event.return_value = (int64_t)frame->rax;
    event.fields_ok = read_settings_blob(&event, inv.settings, inv.sample_count);
    maybe_caller(&event, frame);
    (void)commit_event(&event);
    atomic_fetch_sub_explicit(&g_in_flight, 1, memory_order_seq_cst);
    try_seal();
}

static void on_init_entry(eu4_gfx_capture_frame *frame) {
    if (!begin_admit()) {
        return;
    }
    eu4_gfx_startup_event event;
    memset(&event, 0, sizeof(event));
    event.site = EU4_GFX_STARTUP_SITE_INIT;
    event.phase = 0;
    event.thread_id = thread_id();
    event.admitted = 1;
    event.call_id = atomic_fetch_add_explicit(&g_next_call, 1, memory_order_relaxed);
    void *window = (void *)(uintptr_t)frame->rdi;
    void *success = (void *)(uintptr_t)frame->rsi;
    tls_frame inv = {
        .call_id = event.call_id,
        .site = EU4_GFX_STARTUP_SITE_INIT,
        .settings = NULL,
        .success = success,
        .admitted = 1,
    };
    if (!push_tls(inv)) {
        withdraw_admission();
        return;
    }
    maybe_caller(&event, frame);
    if (window != NULL &&
        safe_read(event.window, window, EU4_GFX_STARTUP_WINDOW_SIZE)) {
        event.window_ok = 1;
    } else {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
    }
    uint8_t probe = 0;
    event.success_dest_readable = success != NULL && safe_read(&probe, success, 1);
    if (!event.success_dest_readable) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
    }
    event.fields_ok = event.window_ok && event.success_dest_readable;
    atomic_fetch_add_explicit(&g_init_calls, 1, memory_order_relaxed);
    (void)commit_event(&event);
}

static void on_init_exit(eu4_gfx_capture_frame *frame) {
    if (g_refuse_depth > 0) {
        g_refuse_depth -= 1;
        return;
    }
    tls_frame inv;
    if (!pop_tls(EU4_GFX_STARTUP_SITE_INIT, &inv)) {
        return;
    }
    eu4_gfx_startup_event event;
    memset(&event, 0, sizeof(event));
    event.site = EU4_GFX_STARTUP_SITE_INIT;
    event.phase = 1;
    event.thread_id = thread_id();
    event.admitted = 1;
    event.call_id = inv.call_id;
    maybe_caller(&event, frame);
    uint8_t success = 0;
    if (inv.success != NULL && safe_read(&success, inv.success, 1)) {
        event.success_readable = 1;
        event.success = success;
    } else {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
    }
    void *master = (void *)(uintptr_t)frame->rax;
    int master_ok = read_master(&event, master);
    event.fields_ok = event.success_readable && master_ok;
    (void)commit_event(&event);
    if (event.success_readable && event.success == 1 && event.master_non_null &&
        event.master_ok) {
        atomic_fetch_add_explicit(&g_qualifying, 1, memory_order_relaxed);
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_seq_cst);
    }
    atomic_fetch_sub_explicit(&g_in_flight, 1, memory_order_seq_cst);
    try_seal();
}

void eu4_gfx_startup_arm(void) {
    eu4_gfx_startup_entry_settings = on_settings_entry;
    eu4_gfx_startup_exit_settings = on_settings_exit;
    eu4_gfx_startup_entry_init = on_init_entry;
    eu4_gfx_startup_exit_init = on_init_exit;
    atomic_store_explicit(&g_state, EU4_GFX_STARTUP_ARMED, memory_order_seq_cst);
}

void eu4_gfx_startup_request_drain(void) {
    uint32_t expected = EU4_GFX_STARTUP_ARMED;
    atomic_compare_exchange_strong_explicit(&g_state, &expected,
                                            EU4_GFX_STARTUP_DRAINING,
                                            memory_order_seq_cst,
                                            memory_order_seq_cst);
    try_seal();
}

uint32_t eu4_gfx_startup_state(void) {
    return atomic_load_explicit(&g_state, memory_order_seq_cst);
}

uint32_t eu4_gfx_startup_in_flight(void) {
    return atomic_load_explicit(&g_in_flight, memory_order_seq_cst);
}

bool eu4_gfx_startup_wait_sealed(unsigned timeout_ms) {
    struct timespec step = {.tv_sec = 0, .tv_nsec = 1000000L};
    for (unsigned i = 0; i < timeout_ms; i++) {
        if (eu4_gfx_startup_state() == EU4_GFX_STARTUP_SEALED) {
            return true;
        }
        try_seal();
        nanosleep(&step, NULL);
    }
    return eu4_gfx_startup_state() == EU4_GFX_STARTUP_SEALED;
}

static int evaluate_complete(const eu4_gfx_startup_snapshot *out) {
    if (out->state != EU4_GFX_STARTUP_SEALED || out->qualifying_init == 0 ||
        out->overflow != 0 || out->unmatched != 0 || out->unavailable != 0 ||
        !out->events_copied) {
        return 0;
    }
    for (uint32_t i = 0; i < out->event_count; i++) {
        if (!out->events[i].fields_ok) {
            return 0;
        }
    }
    if (out->settings_calls > 0) {
        uint32_t entries = 0, exits = 0;
        for (uint32_t i = 0; i < out->event_count; i++) {
            if (out->events[i].site != EU4_GFX_STARTUP_SITE_SETTINGS) {
                continue;
            }
            if (out->events[i].phase == 0) {
                entries++;
            } else {
                exits++;
            }
        }
        if (entries != exits || entries != out->settings_calls) {
            return 0;
        }
    }
    return 1;
}

void eu4_gfx_startup_copy_snapshot(eu4_gfx_startup_snapshot *out) {
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    out->state = eu4_gfx_startup_state();
    out->next_call_id = atomic_load_explicit(&g_next_call, memory_order_relaxed);
    out->overflow = atomic_load_explicit(&g_overflow, memory_order_relaxed);
    out->unmatched = atomic_load_explicit(&g_unmatched, memory_order_relaxed);
    out->refused = atomic_load_explicit(&g_refused, memory_order_relaxed);
    out->unavailable = atomic_load_explicit(&g_unavailable, memory_order_relaxed);
    out->in_flight = atomic_load_explicit(&g_in_flight, memory_order_seq_cst);
    out->settings_calls = atomic_load_explicit(&g_settings_calls, memory_order_relaxed);
    out->init_calls = atomic_load_explicit(&g_init_calls, memory_order_relaxed);
    out->qualifying_init = atomic_load_explicit(&g_qualifying, memory_order_relaxed);
    uint32_t count = atomic_load_explicit(&g_event_count, memory_order_seq_cst);
    if (count > EU4_GFX_STARTUP_EVENT_CAP) {
        count = EU4_GFX_STARTUP_EVENT_CAP;
    }
    out->next_seq = (uint64_t)count + 1;
    if (out->state != EU4_GFX_STARTUP_SEALED) {
        snprintf(out->reason, sizeof(out->reason), "drain_timeout");
        out->event_count = 0;
        out->events_copied = 0;
        out->complete = 0;
        return;
    }
    out->event_count = count;
    memcpy(out->events, g_events, count * sizeof(eu4_gfx_startup_event));
    out->events_copied = 1;
    out->complete = evaluate_complete(out);
}

static void emit_sample_array(FILE *file, const char *name, int values,
                              const eu4_gfx_startup_event *e) {
    fprintf(file, ",\"%s\":[", name);
    for (int32_t i = 0; i < e->sample_captured; i++) {
        if (i) {
            fputc(',', file);
        }
        if (values) {
            fprintf(file, "%u", e->sample_values[i]);
        } else {
            fprintf(file, "%u", e->sample_flags[i]);
        }
    }
    fputs("]", file);
}

static void emit_event_json(FILE *file, const eu4_gfx_startup_event *e) {
    const char *site = e->site == EU4_GFX_STARTUP_SITE_SETTINGS ? "settings" : "init";
    const char *phase = e->phase == 0 ? "entry" : "exit";
    fprintf(file,
            "{\"schema\":\"%s\",\"seq\":%llu,\"call_id\":%llu,\"site\":\"%s\","
            "\"phase\":\"%s\",\"thread_id\":%llu,\"fields_ok\":%s",
            EU4_GFX_STARTUP_SCHEMA, (unsigned long long)e->seq,
            (unsigned long long)e->call_id, site, phase,
            (unsigned long long)e->thread_id, e->fields_ok ? "true" : "false");
    if (e->caller_known) {
        fprintf(file, ",\"caller_rva\":%llu", (unsigned long long)e->caller_rva);
    } else {
        fputs(",\"caller_rva\":null", file);
    }
    if (e->site == EU4_GFX_STARTUP_SITE_SETTINGS) {
        fprintf(file,
                ",\"display\":%d,\"windowed\":%d,\"sample_count\":%d,"
                "\"sample_captured\":%d,\"settings_present\":%s,\"byte_10\":%u,"
                "\"anisotropy\":%u,\"span\":%llu,\"constant\":%u,"
                "\"samples_ok\":%s,\"return\":%lld",
                e->display, e->windowed, e->sample_count, e->sample_captured,
                e->settings_present ? "true" : "false", e->settings_byte10,
                e->anisotropy, (unsigned long long)e->span, e->constant,
                e->samples_ok ? "true" : "false", (long long)e->return_value);
        emit_sample_array(file, "sample_values", 1, e);
        emit_sample_array(file, "sample_flags", 0, e);
    } else {
        fprintf(file,
                ",\"window_ok\":%s,\"success_dest_readable\":%s,"
                "\"success_readable\":%s,\"success\":%u,\"master_non_null\":%s,"
                "\"master_ok\":%s,\"primitive\":%d,\"width\":%d,\"height\":%d,"
                "\"source\":%d,\"dirty\":%u,\"lod\":%u,\"max_texture\":%d,"
                "\"blend_present\":%s,\"depth_present\":%s,\"raster_present\":%s,"
                "\"gl_context_present\":%s,\"viewport\":[%d,%d,%d,%d,%d,%d]",
                e->window_ok ? "true" : "false",
                e->success_dest_readable ? "true" : "false",
                e->success_readable ? "true" : "false", e->success,
                e->master_non_null ? "true" : "false",
                e->master_ok ? "true" : "false", e->primitive, e->width, e->height,
                e->source, e->dirty, e->lod, e->max_texture,
                e->blend_present ? "true" : "false",
                e->depth_present ? "true" : "false",
                e->raster_present ? "true" : "false",
                e->gl_context_present ? "true" : "false", e->viewport[0],
                e->viewport[1], e->viewport[2], e->viewport[3], e->viewport[4],
                e->viewport[5]);
        if (e->window_ok) {
            fputs(",\"window_hex\":\"", file);
            for (size_t i = 0; i < EU4_GFX_STARTUP_WINDOW_SIZE; i++) {
                fprintf(file, "%02x", e->window[i]);
            }
            fputs("\"", file);
        }
    }
    fputs("}\n", file);
}

static int write_should_fail(void) {
    unsigned left = atomic_load_explicit(&g_fail_writes, memory_order_relaxed);
    if (left == 0) {
        return 0;
    }
    atomic_fetch_sub_explicit(&g_fail_writes, 1, memory_order_relaxed);
    return 1;
}

static bool write_text_file(const char *path, int (*body)(FILE *, void *), void *ctx) {
    if (write_should_fail()) {
        return false;
    }
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return false;
    }
    if (body(file, ctx) != 0 || fflush(file) != 0 || fsync(fileno(file)) != 0 ||
        fclose(file) != 0) {
        return false;
    }
    return true;
}

typedef struct {
    const eu4_gfx_startup_snapshot *snap;
    const char *session;
    const char *executable_sha;
    int pid;
    uintptr_t image_base;
} finalize_ctx;

static int write_events_body(FILE *file, void *ctx) {
    finalize_ctx *c = ctx;
    for (uint32_t i = 0; i < c->snap->event_count; i++) {
        emit_event_json(file, &c->snap->events[i]);
    }
    return 0;
}

static int write_meta_body(FILE *file, void *ctx) {
    finalize_ctx *c = ctx;
    fprintf(file,
            "{\"schema\":\"%s\",\"recorder_version\":\"%s\",\"session\":\"%s\","
            "\"pid\":%d,\"executable_sha256\":\"%s\",\"image_base\":\"0x%llx\","
            "\"hook_rvas\":{\"settings\":\"0x%llx\",\"init\":\"0x%llx\"}}\n",
            EU4_GFX_STARTUP_SCHEMA, EU4_GFX_STARTUP_VERSION,
            c->session != NULL ? c->session : "", c->pid,
            c->executable_sha != NULL ? c->executable_sha : "",
            (unsigned long long)c->image_base,
            (unsigned long long)EU4_GFX_STARTUP_RVA_SETTINGS,
            (unsigned long long)EU4_GFX_STARTUP_RVA_INIT);
    return 0;
}

static int write_summary_body(FILE *file, void *ctx) {
    finalize_ctx *c = ctx;
    const eu4_gfx_startup_snapshot *snap = c->snap;
    const char *settings_status =
        snap->settings_calls == 0 ? "not_observed" : "observed";
    fprintf(file,
            "{\"schema\":\"%s\",\"complete\":%s,\"state\":%u,"
            "\"settings\":\"%s\",\"qualifying_init\":%u,\"settings_calls\":%u,"
            "\"init_calls\":%u,\"event_count\":%u,\"overflow\":%u,"
            "\"unmatched\":%u,\"refused\":%u,\"unavailable\":%u,"
            "\"in_flight\":%u,\"events_copied\":%s,\"reason\":\"%s\"}\n",
            EU4_GFX_STARTUP_SCHEMA, snap->complete ? "true" : "false", snap->state,
            settings_status, snap->qualifying_init, snap->settings_calls,
            snap->init_calls, snap->event_count, snap->overflow, snap->unmatched,
            snap->refused, snap->unavailable, snap->in_flight,
            snap->events_copied ? "true" : "false",
            snap->reason[0] != 0 ? snap->reason : "");
    return 0;
}

static bool finalize_owner(const char *run_dir, const char *session,
                           const char *executable_sha, int pid,
                           uintptr_t image_base) {
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    finalize_ctx ctx = {
        .snap = &snap,
        .session = session,
        .executable_sha = executable_sha,
        .pid = pid,
        .image_base = image_base,
    };

    char events_path[1024];
    char summary_path[1024];
    char summary_tmp[1024];
    char meta_path[1024];
    if (snprintf(events_path, sizeof(events_path), "%s/events.jsonl", run_dir) >=
            (int)sizeof(events_path) ||
        snprintf(summary_path, sizeof(summary_path), "%s/summary.json", run_dir) >=
            (int)sizeof(summary_path) ||
        snprintf(summary_tmp, sizeof(summary_tmp), "%s/summary.json.partial",
                 run_dir) >= (int)sizeof(summary_tmp) ||
        snprintf(meta_path, sizeof(meta_path), "%s/metadata.json", run_dir) >=
            (int)sizeof(meta_path)) {
        return false;
    }

    if (snap.events_copied) {
        if (!write_text_file(events_path, write_events_body, &ctx)) {
            return false;
        }
    } else {
        /* Empty events file documents that no sealed copy was taken. */
        if (!write_text_file(events_path, write_events_body, &ctx)) {
            return false;
        }
    }
    if (!write_text_file(meta_path, write_meta_body, &ctx)) {
        return false;
    }
    if (!write_text_file(summary_tmp, write_summary_body, &ctx)) {
        return false;
    }
    if (rename(summary_tmp, summary_path) != 0) {
        return false;
    }
    return true;
}

bool eu4_gfx_startup_finalize(const char *run_dir, const char *session,
                              const char *executable_sha, int pid,
                              uintptr_t image_base) {
    if (run_dir == NULL) {
        return false;
    }
    uint32_t expected = FINALIZE_IDLE;
    if (atomic_compare_exchange_strong_explicit(
            &g_finalize_phase, &expected, FINALIZE_WRITING, memory_order_seq_cst,
            memory_order_seq_cst)) {
        int ok = finalize_owner(run_dir, session, executable_sha, pid, image_base) ? 1
                                                                                   : 0;
        atomic_store_explicit(&g_finalize_ok, ok, memory_order_release);
        atomic_store_explicit(&g_finalize_phase, FINALIZE_DONE, memory_order_release);
        return ok != 0;
    }
    while (atomic_load_explicit(&g_finalize_phase, memory_order_acquire) !=
           FINALIZE_DONE) {
        usleep(1000);
    }
    return atomic_load_explicit(&g_finalize_ok, memory_order_acquire) != 0;
}

void eu4_gfx_startup_test_force_unsafe_read(int on) {
    g_force_unsafe = on ? 1 : 0;
}

void eu4_gfx_startup_test_pause_after_admit(int on) {
    atomic_store_explicit(&g_pause_after_admit, on ? 1 : 0, memory_order_release);
    atomic_store_explicit(&g_admit_paused, 0, memory_order_release);
    atomic_store_explicit(&g_admit_release, 0, memory_order_release);
}

void eu4_gfx_startup_test_release_admit_pause(void) {
    atomic_store_explicit(&g_admit_release, 1, memory_order_release);
}

void eu4_gfx_startup_test_wait_admit_paused(unsigned timeout_ms) {
    for (unsigned i = 0; i < timeout_ms; i++) {
        if (atomic_load_explicit(&g_admit_paused, memory_order_acquire)) {
            return;
        }
        usleep(1000);
    }
}

void eu4_gfx_startup_test_force_tls_full(int on) {
    g_force_tls_full = on ? 1 : 0;
}

void eu4_gfx_startup_test_fail_next_writes(unsigned count) {
    atomic_store_explicit(&g_fail_writes, count, memory_order_relaxed);
}

void eu4_gfx_startup_test_reset(void) {
    atomic_store_explicit(&g_state, EU4_GFX_STARTUP_ARMED, memory_order_seq_cst);
    atomic_store(&g_next_call, 1);
    atomic_store(&g_in_flight, 0);
    atomic_store(&g_overflow, 0);
    atomic_store(&g_unmatched, 0);
    atomic_store(&g_refused, 0);
    atomic_store(&g_unavailable, 0);
    atomic_store(&g_settings_calls, 0);
    atomic_store(&g_init_calls, 0);
    atomic_store(&g_qualifying, 0);
    atomic_store(&g_event_count, 0);
    atomic_store_explicit(&g_finalize_phase, FINALIZE_IDLE, memory_order_seq_cst);
    atomic_store(&g_finalize_ok, 0);
    memset(g_events, 0, sizeof(g_events));
    g_tls.depth = 0;
    g_refuse_depth = 0;
    g_force_unsafe = 0;
    g_force_tls_full = 0;
    atomic_store(&g_pause_after_admit, 0);
    atomic_store(&g_admit_paused, 0);
    atomic_store(&g_admit_release, 0);
    atomic_store(&g_fail_writes, 0);
}

void eu4_gfx_startup_set_image_range(uintptr_t image_base, uintptr_t text_begin,
                                     uintptr_t text_end) {
    g_image_base = image_base;
    g_text_begin = text_begin;
    g_text_end = text_end;
}
