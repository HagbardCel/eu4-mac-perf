#ifndef EU4_GFX_PERF_H
#define EU4_GFX_PERF_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Aggregate, per-frame/run backend counters. The instrumentation is opt-in:
 * every hook is one predictable branch on a process-wide flag, so a normal
 * engine run pays no counter traffic. Nothing is written to disk per draw;
 * a caller reads one snapshot at a frame/run boundary. */

enum {
    EU4_GFX_PERF_FLUSH_CLEAR = 0,
    EU4_GFX_PERF_FLUSH_RESOLVE,
    EU4_GFX_PERF_FLUSH_RENDER_TARGET,
    EU4_GFX_PERF_FLUSH_PRESENT,
    EU4_GFX_PERF_FLUSH_READBACK,
    EU4_GFX_PERF_FLUSH_DRAIN,
    EU4_GFX_PERF_FLUSH_SHUTDOWN,
    EU4_GFX_PERF_FLUSH_GEOMETRY,
    EU4_GFX_PERF_FLUSH_RESOURCE_UPDATE,
    EU4_GFX_PERF_FLUSH_DIAGNOSTIC,
    EU4_GFX_PERF_FLUSH_CAPACITY,
    EU4_GFX_PERF_FLUSH_REASONS
};

typedef struct {
    uint64_t draws_encoded;
    uint64_t draws_generic;
    uint64_t draws_simple;
    uint64_t draws_sprite;
    uint64_t command_buffers_created;
    uint64_t command_buffers_committed;
    uint64_t render_encoders_created;
    uint64_t render_encoders_reused;
    uint64_t index_temp_allocs;
    uint64_t index_temp_bytes;
    uint64_t pipeline_hits;
    uint64_t pipeline_misses;
    uint64_t sampling_conversions;
    uint64_t completion_waits;
    uint64_t batch_flushes;
    uint64_t cpu_encode_ns;
    /* GPU duration of every tracked command buffer, counted once per commit.
       Records are only attributed to the frame epoch in which they were
       registered; late completion handlers cannot contaminate a reset frame. */
    uint64_t gpu_execution_ns;
    uint64_t gpu_timing_records;
    uint64_t gpu_timing_pending;
    uint64_t flushes[EU4_GFX_PERF_FLUSH_REASONS];
} eu4_gfx_perf_snapshot;

/* Process-wide switch. Off by default; the engine path leaves it off. */
void eu4_gfx_perf_set_enabled(int on);
int eu4_gfx_perf_enabled(void);
void eu4_gfx_perf_reset(void);
void eu4_gfx_perf_read(eu4_gfx_perf_snapshot *out);

/* Writes a compact JSON object. Returns the length written (excluding the
 * terminator) or 0 when the buffer is too small. */
size_t eu4_gfx_perf_snapshot_json(char *out, size_t capacity);
const char *eu4_gfx_perf_flush_reason_name(int reason);

/* Hooks. All are no-ops while instrumentation is disabled. */
void eu4_gfx_perf_draw_encoded(int route); /* 0 generic, 1 simple, 2 sprite */
void eu4_gfx_perf_command_buffer_created(void);
void eu4_gfx_perf_command_buffer_committed(void);
void eu4_gfx_perf_render_encoder_created(void);
void eu4_gfx_perf_render_encoder_reused(void);
void eu4_gfx_perf_index_temp_alloc(size_t bytes);
void eu4_gfx_perf_pipeline(int hit);
void eu4_gfx_perf_sampling_conversion(void);
void eu4_gfx_perf_completion_wait(void);
void eu4_gfx_perf_batch_flush(int reason);
/* Registers GPU timing once for a command buffer before commit. The completed
   handler adds its duration to the registering frame only. */
void eu4_gfx_perf_track_command_buffer(void *command_buffer);
/* Waits until every registered completion handler has run. Returns 1 when the
   timing records are complete, 0 on timeout. Benchmark/diagnostic only. */
int eu4_gfx_perf_wait_gpu_timing(uint32_t timeout_ms);
/* Legacy direct hook retained for hosts that report an external interval. */
void eu4_gfx_perf_gpu_execution(uint64_t nanoseconds);

/* CPU encoding timer. begin returns an opaque timestamp; end adds the elapsed
 * monotonic nanoseconds to cpu_encode_ns. Both are safe while disabled. */
uint64_t eu4_gfx_perf_encode_begin(void);
void eu4_gfx_perf_encode_end(uint64_t start);

#ifndef EU4_GFX_PERF_IMPLEMENTATION
/* Link-time fallback for the small offline lifecycle hosts that build a subset
 * of the backend modules. The real implementation lives in eu4_gfx_perf.mm and
 * overrides these weak no-ops whenever it is linked into the image. */
#if defined(__GNUC__)
#define EU4_GFX_PERF_WEAK __attribute__((weak))
#else
#define EU4_GFX_PERF_WEAK
#endif
EU4_GFX_PERF_WEAK void eu4_gfx_perf_set_enabled(int on) { (void)on; }
EU4_GFX_PERF_WEAK int eu4_gfx_perf_enabled(void) { return 0; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_reset(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_read(eu4_gfx_perf_snapshot *out) {
    if (out != NULL) {
        *out = (eu4_gfx_perf_snapshot){0};
    }
}
EU4_GFX_PERF_WEAK size_t eu4_gfx_perf_snapshot_json(char *out, size_t capacity) {
    (void)out;
    (void)capacity;
    return 0;
}
EU4_GFX_PERF_WEAK const char *eu4_gfx_perf_flush_reason_name(int reason) {
    (void)reason;
    return "unknown";
}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_draw_encoded(int route) { (void)route; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_command_buffer_created(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_command_buffer_committed(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_render_encoder_created(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_render_encoder_reused(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_index_temp_alloc(size_t bytes) { (void)bytes; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_pipeline(int hit) { (void)hit; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_sampling_conversion(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_completion_wait(void) {}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_batch_flush(int reason) { (void)reason; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_gpu_execution(uint64_t nanoseconds) {
    (void)nanoseconds;
}
EU4_GFX_PERF_WEAK void eu4_gfx_perf_track_command_buffer(void *command_buffer) {
    (void)command_buffer;
}
EU4_GFX_PERF_WEAK int eu4_gfx_perf_wait_gpu_timing(uint32_t timeout_ms) {
    (void)timeout_ms;
    return 1;
}
EU4_GFX_PERF_WEAK uint64_t eu4_gfx_perf_encode_begin(void) { return 0; }
EU4_GFX_PERF_WEAK void eu4_gfx_perf_encode_end(uint64_t start) { (void)start; }
#undef EU4_GFX_PERF_WEAK
#endif

#ifdef __cplusplus
}
#endif

#endif
