#define EU4_GFX_PERF_IMPLEMENTATION 1
#include "eu4_gfx_perf.h"

#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#import <Metal/Metal.h>

/* Plain ints, not atomics: the engine mutates these on the single GFX thread,
 * and the benchmark host reads them after a drain. The enabled flag is only
 * written before any workload starts. */
static int g_enabled;
static eu4_gfx_perf_snapshot g_counters;

/* GPU completion handlers run off the graphics thread. A frame epoch makes a
   late handler from a previous benchmark frame harmless. */
static pthread_mutex_t g_gpu_lock = PTHREAD_MUTEX_INITIALIZER;
static uint64_t g_gpu_epoch;
static uint64_t g_gpu_ns;
static uint64_t g_gpu_records;
static uint64_t g_gpu_pending;

static void reset_counters(void) {
    memset(&g_counters, 0, sizeof g_counters);
}

void eu4_gfx_perf_set_enabled(int on) {
    g_enabled = on != 0;
}

int eu4_gfx_perf_enabled(void) {
    return g_enabled;
}

void eu4_gfx_perf_reset(void) {
    pthread_mutex_lock(&g_gpu_lock);
    g_gpu_epoch++;
    g_gpu_ns = 0;
    g_gpu_records = 0;
    g_gpu_pending = 0;
    pthread_mutex_unlock(&g_gpu_lock);
    reset_counters();
}

void eu4_gfx_perf_read(eu4_gfx_perf_snapshot *out) {
    if (out == NULL) {
        return;
    }
    *out = g_counters;
    pthread_mutex_lock(&g_gpu_lock);
    out->gpu_execution_ns = g_gpu_ns;
    out->gpu_timing_records = g_gpu_records;
    out->gpu_timing_pending = g_gpu_pending;
    pthread_mutex_unlock(&g_gpu_lock);
}

void eu4_gfx_perf_track_command_buffer(void *command_buffer) {
    if (!g_enabled || command_buffer == NULL) {
        return;
    }
    id<MTLCommandBuffer> command = (__bridge id<MTLCommandBuffer>)command_buffer;
    if (command == nil) {
        return;
    }
    pthread_mutex_lock(&g_gpu_lock);
    uint64_t epoch = g_gpu_epoch;
    g_gpu_pending++;
    pthread_mutex_unlock(&g_gpu_lock);
    [command addCompletedHandler:^(id<MTLCommandBuffer> completed) {
        double start = completed.GPUStartTime;
        double end = completed.GPUEndTime;
        pthread_mutex_lock(&g_gpu_lock);
        if (epoch == g_gpu_epoch) {
            if (g_gpu_pending > 0) {
                g_gpu_pending--;
            }
            if (start > 0.0 && end > start) {
                g_gpu_ns += (uint64_t)((end - start) * 1e9);
                g_gpu_records++;
            }
        }
        pthread_mutex_unlock(&g_gpu_lock);
    }];
}

int eu4_gfx_perf_wait_gpu_timing(uint32_t timeout_ms) {
    if (!g_enabled) {
        return 1;
    }
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC_RAW, &start);
    uint64_t deadline = (uint64_t)start.tv_sec * 1000000000ull +
                        (uint64_t)start.tv_nsec +
                        (uint64_t)timeout_ms * 1000000ull;
    for (;;) {
        pthread_mutex_lock(&g_gpu_lock);
        uint64_t pending = g_gpu_pending;
        pthread_mutex_unlock(&g_gpu_lock);
        if (pending == 0) {
            return 1;
        }
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC_RAW, &now);
        uint64_t at = (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
        if (at >= deadline) {
            return 0;
        }
        usleep(200);
    }
}

void eu4_gfx_perf_draw_encoded(int route) {
    if (!g_enabled) {
        return;
    }
    g_counters.draws_encoded++;
    if (route == 1) {
        g_counters.draws_simple++;
    } else if (route == 2) {
        g_counters.draws_sprite++;
    } else {
        g_counters.draws_generic++;
    }
}

void eu4_gfx_perf_command_buffer_created(void) {
    if (g_enabled) {
        g_counters.command_buffers_created++;
    }
}

void eu4_gfx_perf_command_buffer_committed(void) {
    if (g_enabled) {
        g_counters.command_buffers_committed++;
    }
}

void eu4_gfx_perf_render_encoder_created(void) {
    if (g_enabled) {
        g_counters.render_encoders_created++;
    }
}

void eu4_gfx_perf_render_encoder_reused(void) {
    if (g_enabled) {
        g_counters.render_encoders_reused++;
    }
}

void eu4_gfx_perf_index_temp_alloc(size_t bytes) {
    if (g_enabled) {
        g_counters.index_temp_allocs++;
        g_counters.index_temp_bytes += bytes;
    }
}

void eu4_gfx_perf_pipeline(int hit) {
    if (!g_enabled) {
        return;
    }
    if (hit) {
        g_counters.pipeline_hits++;
    } else {
        g_counters.pipeline_misses++;
    }
}

void eu4_gfx_perf_sampling_conversion(void) {
    if (g_enabled) {
        g_counters.sampling_conversions++;
    }
}

void eu4_gfx_perf_completion_wait(void) {
    if (g_enabled) {
        g_counters.completion_waits++;
    }
}

void eu4_gfx_perf_batch_flush(int reason) {    if (!g_enabled) {
        return;
    }
    if (reason < 0 || reason >= EU4_GFX_PERF_FLUSH_REASONS) {
        reason = EU4_GFX_PERF_FLUSH_CAPACITY;
    }
    g_counters.batch_flushes++;
    g_counters.flushes[reason]++;
}

void eu4_gfx_perf_gpu_execution(uint64_t nanoseconds) {
    if (!g_enabled) {
        return;
    }
    pthread_mutex_lock(&g_gpu_lock);
    g_gpu_ns += nanoseconds;
    g_gpu_records++;
    pthread_mutex_unlock(&g_gpu_lock);
}

uint64_t eu4_gfx_perf_encode_begin(void) {
    if (!g_enabled) {
        return 0;
    }
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
        return 0;
    }
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

void eu4_gfx_perf_encode_end(uint64_t start) {
    if (!g_enabled || start == 0) {
        return;
    }
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
        return;
    }
    uint64_t now = (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
    if (now > start) {
        g_counters.cpu_encode_ns += now - start;
    }
}

const char *eu4_gfx_perf_flush_reason_name(int reason) {
    switch (reason) {
        case EU4_GFX_PERF_FLUSH_CLEAR: return "clear";
        case EU4_GFX_PERF_FLUSH_RESOLVE: return "resolve";
        case EU4_GFX_PERF_FLUSH_RENDER_TARGET: return "render_target";
        case EU4_GFX_PERF_FLUSH_PRESENT: return "present";
        case EU4_GFX_PERF_FLUSH_READBACK: return "readback";
        case EU4_GFX_PERF_FLUSH_DRAIN: return "drain";
        case EU4_GFX_PERF_FLUSH_SHUTDOWN: return "shutdown";
        case EU4_GFX_PERF_FLUSH_GEOMETRY: return "geometry";
        case EU4_GFX_PERF_FLUSH_RESOURCE_UPDATE: return "resource_update";
        case EU4_GFX_PERF_FLUSH_DIAGNOSTIC: return "diagnostic";
        case EU4_GFX_PERF_FLUSH_CAPACITY: return "capacity";
        default: return "unknown";
    }
}

size_t eu4_gfx_perf_snapshot_json(char *out, size_t capacity) {
    if (out == NULL || capacity == 0) {
        return 0;
    }
    eu4_gfx_perf_snapshot snapshot;
    eu4_gfx_perf_read(&snapshot);
    const eu4_gfx_perf_snapshot *c = &snapshot;
    char reasons[512];
    reasons[0] = 0;
    size_t used = 0;
    for (int i = 0; i < EU4_GFX_PERF_FLUSH_REASONS; i++) {
        int n = snprintf(reasons + used, sizeof reasons - used, "%s\"%s\":%llu",
                         i == 0 ? "" : ",", eu4_gfx_perf_flush_reason_name(i),
                         (unsigned long long)c->flushes[i]);
        if (n < 0 || (size_t)n >= sizeof reasons - used) {
            reasons[used] = 0;
            break;
        }
        used += (size_t)n;
    }
    int n = snprintf(
        out, capacity,
        "{\"schema\":\"gfx-perf/v1\",\"draws_encoded\":%llu,"
        "\"draws_generic\":%llu,\"draws_simple\":%llu,\"draws_sprite\":%llu,"
        "\"command_buffers_created\":%llu,\"command_buffers_committed\":%llu,"
        "\"render_encoders_created\":%llu,\"render_encoders_reused\":%llu,"
        "\"index_temp_allocs\":%llu,\"index_temp_bytes\":%llu,"
        "\"pipeline_hits\":%llu,\"pipeline_misses\":%llu,"
        "\"sampling_conversions\":%llu,\"completion_waits\":%llu,"
        "\"batch_flushes\":%llu,\"cpu_encode_ns\":%llu,\"gpu_execution_ns\":%llu,"
        "\"gpu_timing_records\":%llu,\"gpu_timing_pending\":%llu,"
        "\"flush_reasons\":{%s}}",
        (unsigned long long)c->draws_encoded,
        (unsigned long long)c->draws_generic,
        (unsigned long long)c->draws_simple,
        (unsigned long long)c->draws_sprite,
        (unsigned long long)c->command_buffers_created,
        (unsigned long long)c->command_buffers_committed,
        (unsigned long long)c->render_encoders_created,
        (unsigned long long)c->render_encoders_reused,
        (unsigned long long)c->index_temp_allocs,
        (unsigned long long)c->index_temp_bytes,
        (unsigned long long)c->pipeline_hits,
        (unsigned long long)c->pipeline_misses,
        (unsigned long long)c->sampling_conversions,
        (unsigned long long)c->completion_waits,
        (unsigned long long)c->batch_flushes,
        (unsigned long long)c->cpu_encode_ns,
        (unsigned long long)c->gpu_execution_ns,
        (unsigned long long)c->gpu_timing_records,
        (unsigned long long)c->gpu_timing_pending,
        reasons);
    if (n < 0 || (size_t)n >= capacity) {
        out[0] = 0;
        return 0;
    }
    return (size_t)n;
}
