#ifndef EU4_GFX_METAL_REPLAY_H
#define EU4_GFX_METAL_REPLAY_H

/* Reusable Metal frame-replay core and stable C ABI.
 *
 * The core reads one immutable ``gfx-frame-trace/v1`` directory and executes
 * its ordered operations through Metal.  It owns its device, command queue,
 * pipelines, textures, samplers, render targets and provenance for the life of
 * the session.  The standalone CLI and the in-process shadow bridge are thin
 * callers of this interface; neither reimplements an operation.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define EU4_METAL_REPLAY_ABI_VERSION 1u
#define EU4_METAL_REPLAY_PATH_MAX 1024u
#define EU4_METAL_REPLAY_SHA_MAX 65u

typedef struct eu4_metal_replay_session eu4_metal_replay_session;

typedef struct {
    uint32_t struct_size;
    uint32_t abi_version;
    const char *frame_dir;    /* required */
    const char *shader_dir;   /* required: contains shaders.json */
    const char *output_dir;   /* required; created if absent */
    const char *checkpoint_path; /* optional */
    int32_t stop_after;  /* -1: all events */
    int32_t only_event;  /* -1: full frame */
    uint32_t seed_marked;
    uint32_t capture_post_images;
    uint32_t capture_pre_images;
    uint32_t depth_from_prefix;
    uint64_t timeout_ms; /* 0: unbounded */
} eu4_metal_replay_config;

typedef struct {
    uint32_t struct_size;
    uint32_t abi_version;
    int32_t ok;             /* 1: every event encoded, readback succeeded */
    int32_t command_status; /* MTLCommandBufferStatus of the final buffer */
    int32_t readback_ok;    /* 1: metal-frame.bgra was written */
    uint32_t events;        /* events in the trace */
    uint32_t encoded;       /* events attempted */
    uint32_t failed;        /* events that failed to encode */
    uint64_t duration_ns;   /* wall time of this frame */
    char error[512];
    char result_json[EU4_METAL_REPLAY_PATH_MAX];
    char metal_frame[EU4_METAL_REPLAY_PATH_MAX];
    char provenance_jsonl[EU4_METAL_REPLAY_PATH_MAX];
    char manifest_reference_sha256[EU4_METAL_REPLAY_SHA_MAX];
    char metal_frame_sha256[EU4_METAL_REPLAY_SHA_MAX];
} eu4_metal_replay_result;

/* Creates a session.  Returns NULL on failure and fills ``error`` when given. */
eu4_metal_replay_session *eu4_metal_replay_create(
    const eu4_metal_replay_config *config, char *error, size_t error_size);

/* Executes exactly one captured frame.  The session may be reused for another
 * frame; renderer-generated resource history is retained in the session. */
int eu4_metal_replay_frame(eu4_metal_replay_session *session,
                           const eu4_metal_replay_config *config,
                           eu4_metal_replay_result *result);

void eu4_metal_replay_destroy(eu4_metal_replay_session *session);

const char *eu4_metal_replay_last_error(eu4_metal_replay_session *session);

#ifdef __cplusplus
}
#endif

#endif
