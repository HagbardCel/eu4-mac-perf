#include "eu4_gfx_startup_trace.h"

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

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
static _Atomic uint64_t g_next_seq = 1;
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
    /* Return address sits above the 648-byte gateway frame. */
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

static bool commit_event(eu4_gfx_startup_event *event) {
    uint32_t index = atomic_fetch_add_explicit(&g_event_count, 1, memory_order_acq_rel);
    if (index >= EU4_GFX_STARTUP_EVENT_CAP) {
        atomic_fetch_add_explicit(&g_overflow, 1, memory_order_relaxed);
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_release);
        return false;
    }
    event->seq = atomic_fetch_add_explicit(&g_next_seq, 1, memory_order_relaxed);
    g_events[index] = *event;
    return true;
}

static bool push_tls(tls_frame frame) {
    if (g_tls.depth >= EU4_GFX_STARTUP_TLS_DEPTH) {
        atomic_fetch_add_explicit(&g_overflow, 1, memory_order_relaxed);
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_release);
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

static void try_seal(void) {
    if (atomic_load_explicit(&g_state, memory_order_acquire) != EU4_GFX_STARTUP_DRAINING) {
        return;
    }
    if (atomic_load_explicit(&g_in_flight, memory_order_acquire) == 0) {
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_SEALED, memory_order_release);
    }
}

static int read_settings_blob(eu4_gfx_startup_event *event, void *settings, int32_t count) {
    if (settings == NULL) {
        event->settings_present = 0;
        return 0;
    }
    event->settings_present = 1;
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
    int32_t use = count;
    if (use < 0) {
        use = 0;
    }
    if (use > (int32_t)EU4_GFX_STARTUP_SAMPLE_CAP) {
        use = (int32_t)EU4_GFX_STARTUP_SAMPLE_CAP;
    }
    event->sample_count = use;
    if (use == 0) {
        event->samples_ok = 1;
        return 1;
    }
    if (array == NULL) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    uint8_t records[EU4_GFX_STARTUP_SAMPLE_CAP * EU4_GFX_STARTUP_SAMPLE_SIZE];
    if (!safe_read(records, array, (size_t)use * EU4_GFX_STARTUP_SAMPLE_SIZE)) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
        return 0;
    }
    for (int32_t i = 0; i < use; i++) {
        memcpy(&event->sample_values[i], records + (size_t)i * EU4_GFX_STARTUP_SAMPLE_SIZE, 4);
        event->sample_flags[i] = records[(size_t)i * EU4_GFX_STARTUP_SAMPLE_SIZE + 4];
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

static void refuse_entry(void) {
    g_refuse_depth += 1;
    atomic_fetch_add_explicit(&g_refused, 1, memory_order_relaxed);
}

static void on_settings_entry(eu4_gfx_capture_frame *frame) {
    uint32_t state = atomic_load_explicit(&g_state, memory_order_acquire);
    if (state != EU4_GFX_STARTUP_ARMED) {
        refuse_entry();
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
    event.sample_count = (int32_t)frame->rdx;
    void *settings = (void *)(uintptr_t)frame->rcx;
    maybe_caller(&event, frame);
    event.fields_ok = read_settings_blob(&event, settings, event.sample_count);
    tls_frame inv = {
        .call_id = event.call_id,
        .site = EU4_GFX_STARTUP_SITE_SETTINGS,
        .settings = settings,
        .success = NULL,
        .sample_count = event.sample_count,
        .admitted = 1,
    };
    if (!push_tls(inv)) {
        refuse_entry();
        return;
    }
    atomic_fetch_add_explicit(&g_in_flight, 1, memory_order_acq_rel);
    atomic_fetch_add_explicit(&g_settings_calls, 1, memory_order_relaxed);
    if (!commit_event(&event)) {
        /* Keep TLS so exit can still drain. */
    }
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
    event.sample_count = inv.sample_count;
    event.fields_ok = read_settings_blob(&event, inv.settings, inv.sample_count);
    maybe_caller(&event, frame);
    (void)commit_event(&event);
    atomic_fetch_sub_explicit(&g_in_flight, 1, memory_order_acq_rel);
    try_seal();
}

static void on_init_entry(eu4_gfx_capture_frame *frame) {
    uint32_t state = atomic_load_explicit(&g_state, memory_order_acquire);
    if (state != EU4_GFX_STARTUP_ARMED) {
        refuse_entry();
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
    tls_frame inv = {
        .call_id = event.call_id,
        .site = EU4_GFX_STARTUP_SITE_INIT,
        .settings = NULL,
        .success = success,
        .admitted = 1,
    };
    if (!push_tls(inv)) {
        refuse_entry();
        return;
    }
    atomic_fetch_add_explicit(&g_in_flight, 1, memory_order_acq_rel);
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
        atomic_store_explicit(&g_state, EU4_GFX_STARTUP_DRAINING, memory_order_release);
    }
    atomic_fetch_sub_explicit(&g_in_flight, 1, memory_order_acq_rel);
    try_seal();
}

void eu4_gfx_startup_arm(void) {
    eu4_gfx_startup_entry_settings = on_settings_entry;
    eu4_gfx_startup_exit_settings = on_settings_exit;
    eu4_gfx_startup_entry_init = on_init_entry;
    eu4_gfx_startup_exit_init = on_init_exit;
    atomic_store_explicit(&g_state, EU4_GFX_STARTUP_ARMED, memory_order_release);
}

void eu4_gfx_startup_request_drain(void) {
    uint32_t expected = EU4_GFX_STARTUP_ARMED;
    atomic_compare_exchange_strong_explicit(&g_state, &expected,
                                            EU4_GFX_STARTUP_DRAINING,
                                            memory_order_acq_rel,
                                            memory_order_acquire);
    try_seal();
}

uint32_t eu4_gfx_startup_state(void) {
    return atomic_load_explicit(&g_state, memory_order_acquire);
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

void eu4_gfx_startup_copy_snapshot(eu4_gfx_startup_snapshot *out) {
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    out->state = eu4_gfx_startup_state();
    out->next_seq = atomic_load_explicit(&g_next_seq, memory_order_relaxed);
    out->next_call_id = atomic_load_explicit(&g_next_call, memory_order_relaxed);
    out->overflow = atomic_load_explicit(&g_overflow, memory_order_relaxed);
    out->unmatched = atomic_load_explicit(&g_unmatched, memory_order_relaxed);
    out->refused = atomic_load_explicit(&g_refused, memory_order_relaxed);
    out->unavailable = atomic_load_explicit(&g_unavailable, memory_order_relaxed);
    out->in_flight = atomic_load_explicit(&g_in_flight, memory_order_relaxed);
    out->settings_calls = atomic_load_explicit(&g_settings_calls, memory_order_relaxed);
    out->init_calls = atomic_load_explicit(&g_init_calls, memory_order_relaxed);
    out->qualifying_init = atomic_load_explicit(&g_qualifying, memory_order_relaxed);
    uint32_t count = atomic_load_explicit(&g_event_count, memory_order_acquire);
    if (count > EU4_GFX_STARTUP_EVENT_CAP) {
        count = EU4_GFX_STARTUP_EVENT_CAP;
    }
    out->event_count = count;
    memcpy(out->events, g_events, count * sizeof(eu4_gfx_startup_event));
    int complete = out->state == EU4_GFX_STARTUP_SEALED && out->qualifying_init > 0 &&
                   out->overflow == 0 && out->unmatched == 0 && out->unavailable == 0;
    if (out->settings_calls > 0) {
        /* Every settings call needs a matched exit in the committed set. */
        uint32_t entries = 0, exits = 0;
        for (uint32_t i = 0; i < count; i++) {
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
            complete = 0;
        }
    }
    out->complete = complete;
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
                "\"settings_present\":%s,\"byte_10\":%u,\"anisotropy\":%u,"
                "\"span\":%llu,\"constant\":%u,\"samples_ok\":%s,\"return\":%lld",
                e->display, e->windowed, e->sample_count,
                e->settings_present ? "true" : "false", e->settings_byte10,
                e->anisotropy, (unsigned long long)e->span, e->constant,
                e->samples_ok ? "true" : "false", (long long)e->return_value);
        fputs(",\"sample_flags\":[", file);
        for (int32_t i = 0; i < e->sample_count; i++) {
            if (i) {
                fputc(',', file);
            }
            fprintf(file, "%u", e->sample_flags[i]);
        }
        fputs("]", file);
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

bool eu4_gfx_startup_finalize(const char *run_dir, const char *session,
                              const char *executable_sha, int pid,
                              uintptr_t image_base) {
    if (run_dir == NULL) {
        return false;
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);

    char events_path[1024];
    char summary_path[1024];
    char meta_path[1024];
    if (snprintf(events_path, sizeof(events_path), "%s/events.jsonl", run_dir) >=
            (int)sizeof(events_path) ||
        snprintf(summary_path, sizeof(summary_path), "%s/summary.json", run_dir) >=
            (int)sizeof(summary_path) ||
        snprintf(meta_path, sizeof(meta_path), "%s/metadata.json", run_dir) >=
            (int)sizeof(meta_path)) {
        return false;
    }

    FILE *events = fopen(events_path, "w");
    if (events == NULL) {
        return false;
    }
    /* Order by seq. Events are appended in commit order which matches seq. */
    for (uint32_t i = 0; i < snap.event_count; i++) {
        emit_event_json(events, &snap.events[i]);
    }
    if (fflush(events) != 0 || fsync(fileno(events)) != 0 || fclose(events) != 0) {
        return false;
    }

    FILE *meta = fopen(meta_path, "w");
    if (meta == NULL) {
        return false;
    }
    fprintf(meta,
            "{\"schema\":\"%s\",\"recorder_version\":\"%s\",\"session\":\"%s\","
            "\"pid\":%d,\"executable_sha256\":\"%s\",\"image_base\":\"0x%llx\","
            "\"hook_rvas\":{\"settings\":\"0x%llx\",\"init\":\"0x%llx\"}}\n",
            EU4_GFX_STARTUP_SCHEMA, EU4_GFX_STARTUP_VERSION,
            session != NULL ? session : "", pid,
            executable_sha != NULL ? executable_sha : "",
            (unsigned long long)image_base,
            (unsigned long long)EU4_GFX_STARTUP_RVA_SETTINGS,
            (unsigned long long)EU4_GFX_STARTUP_RVA_INIT);
    if (fflush(meta) != 0 || fsync(fileno(meta)) != 0 || fclose(meta) != 0) {
        return false;
    }

    const char *settings_status =
        snap.settings_calls == 0 ? "not_observed" : "observed";
    FILE *summary = fopen(summary_path, "w");
    if (summary == NULL) {
        return false;
    }
    fprintf(summary,
            "{\"schema\":\"%s\",\"complete\":%s,\"state\":%u,"
            "\"settings\":\"%s\",\"qualifying_init\":%u,\"settings_calls\":%u,"
            "\"init_calls\":%u,\"event_count\":%u,\"overflow\":%u,"
            "\"unmatched\":%u,\"refused\":%u,\"unavailable\":%u,"
            "\"in_flight\":%u}\n",
            EU4_GFX_STARTUP_SCHEMA, snap.complete ? "true" : "false", snap.state,
            settings_status, snap.qualifying_init, snap.settings_calls,
            snap.init_calls, snap.event_count, snap.overflow, snap.unmatched,
            snap.refused, snap.unavailable, snap.in_flight);
    if (fflush(summary) != 0 || fsync(fileno(summary)) != 0 || fclose(summary) != 0) {
        return false;
    }
    return true;
}

void eu4_gfx_startup_test_force_unsafe_read(int on) {
    g_force_unsafe = on ? 1 : 0;
}

void eu4_gfx_startup_test_reset(void) {
    atomic_store(&g_state, EU4_GFX_STARTUP_ARMED);
    atomic_store(&g_next_seq, 1);
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
    memset(g_events, 0, sizeof(g_events));
    g_tls.depth = 0;
    g_refuse_depth = 0;
    g_force_unsafe = 0;
}

void eu4_gfx_startup_set_image_range(uintptr_t image_base, uintptr_t text_begin,
                                     uintptr_t text_end) {
    g_image_base = image_base;
    g_text_begin = text_begin;
    g_text_end = text_end;
}
