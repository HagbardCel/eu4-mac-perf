#ifndef EU4_GFX_FRAME_SEAL_H
#define EU4_GFX_FRAME_SEAL_H

/* Authoritative same-process frame handoff.
 *
 * The frame tracer publishes one immutable ``gfx-frame-trace/v1`` directory and,
 * when sealing is enabled (``EU4_GFX_FRAME_SEAL=1``), a ``frame-seal.json``
 * descriptor.  A consumer registered with
 * :func:`eu4_gfx_frame_sealed_subscribe` is invoked exactly once for a complete
 * frame, after the reference readback, event-stream validation and manifest
 * publication have succeeded and after the trace writer's mutex has been
 * released.
 *
 * The descriptor passed to the callback is a private copy owned by the tracer
 * and is valid only for the duration of that callback.  Copy any field that must
 * outlive it.  The callback runs on the engine thread that reached the frame
 * boundary; it must not block indefinitely and must not re-enter the tracer.
 * A re-entrant notification is rejected and counted.  A non-zero callback
 * return value is recorded, but the frame remains sealed and is not
 * re-notified.
 *
 * This is provenance for one captured execution.  It is deliberately not a
 * cross-process matching key.
 */

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define EU4_FRAME_CAPTURE_ABI_VERSION 2u
#define EU4_FRAME_CAPTURE_PATH_MAX 1024u
#define EU4_FRAME_CAPTURE_SHA_MAX 65u
#define EU4_FRAME_SEAL_SCHEMA "gfx-frame-seal/v1"

typedef struct eu4_frame_capture_descriptor {
    /* ABI guard: always the first two members. */
    uint32_t struct_size;
    uint32_t abi_version;

    /* Identity of this one captured execution. */
    uint64_t capture_frame_id; /* monotonic for this launched process */
    char session_id[64];
    int32_t pid;
    int32_t reserved;
    uint64_t process_start_ns;

    /* Frame boundary and selected indices. */
    char boundary[32]; /* "present", "scene" or "boundary_clear" */
    uint32_t present_index;
    uint32_t scene_index;

    /* Ordered operation accounting. */
    uint64_t operation_count;
    uint64_t draw_count;
    uint64_t clear_count;
    uint64_t copy_count; /* resolve/copy events in the ordered stream */

    /* Immutable event stream, hashed only after it was closed and validated. */
    uint64_t event_stream_bytes;
    uint32_t event_stream_verified;
    uint32_t reserved_stream;
    char event_stream_file[128];
    char event_stream_sha256[EU4_FRAME_CAPTURE_SHA_MAX];

    /* Original render target and pixel format. */
    uint32_t width;
    uint32_t height;
    char pixel_format[16]; /* "BGRA8", lower-left origin, tightly packed */

    /* Completeness and integrity. */
    uint32_t complete;        /* 1 only when the frame is sealed */
    uint32_t trace_complete;  /* frame-trace.json complete */
    uint32_t trace_integrity; /* event stream validated and counted */
    uint32_t readback_ok;     /* original GL reference written and verified */

    /* Original OpenGL reference. */
    uint64_t reference_bytes;
    char reference_file[128]; /* relative to capture_dir */
    char reference_sha256[EU4_FRAME_CAPTURE_SHA_MAX];

    /* Immutable trace manifest. */
    char manifest_file[128]; /* relative to capture_dir */
    char manifest_sha256[EU4_FRAME_CAPTURE_SHA_MAX];

    /* Sealed directory. */
    char capture_dir[EU4_FRAME_CAPTURE_PATH_MAX];

    /* GPU/readback and diagnostics. */
    char gpu_status[64]; /* e.g. "gl_readpixels_synchronous" */
    char unsupported_dependencies[256]; /* ';'-separated, empty when none */
    char failure_reason[64];            /* empty when complete */

    /* One-shot cost of sealing, for overhead accounting.  ``seal_ns`` is the
     * total from the reference-readback start through seal preparation and
     * therefore includes ``readback_ns`` and ``finalize_ns``. */
    uint64_t readback_ns;
    uint64_t finalize_ns;
    uint64_t seal_ns;
} eu4_frame_capture_descriptor;

/* Returns 0 on success (the frame was sealed), 1 on a rejected callback status,
 * or another non-zero value for a consumer-reported failure. */
typedef int (*eu4_frame_sealed_callback)(
    const eu4_frame_capture_descriptor *capture, void *context);

/* Registers the one consumer.  Returns 0 on success, -1 when a callback is
 * already registered or the argument is NULL.  Registration is process-wide and
 * is not cleared by reconfiguring the tracer. */
int eu4_gfx_frame_sealed_subscribe(eu4_frame_sealed_callback callback,
                                   void *context);

/* Removes the consumer.  A notification already in flight still completes. */
void eu4_gfx_frame_sealed_unsubscribe(void);

/* Returns 1 when a sealed descriptor is waiting to be dispatched.  Consumers
 * do not need this; the tracer drains it from the frame hooks. */
int eu4_gfx_frame_sealed_pending(void);

/* Dispatches a pending sealed descriptor, if any.  Safe to call when nothing is
 * pending.  A call made from inside the consumer is rejected. */
void eu4_gfx_frame_sealed_dispatch(void);

/* Instrumentation counters, read after the run.  ``notifications`` counts
 * consumer deliveries (a non-zero callback status is recorded separately and
 * is not re-delivered). */
uint32_t eu4_gfx_frame_sealed_notifications(void);
uint32_t eu4_gfx_frame_sealed_reentrant_rejections(void);
int eu4_gfx_frame_sealed_last_callback_status(void);
int eu4_gfx_frame_sealed_last_seal_written(void);

#ifdef __cplusplus
}
#endif

#endif
