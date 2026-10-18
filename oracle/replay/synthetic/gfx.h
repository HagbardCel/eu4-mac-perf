/* Trusted interface for the Oracle P4b synthetic *graphics* subject.
 *
 * A small, portable C11 library that models the shape of a graphics subsystem:
 * typed GPU-like resources (textures and vertex buffers) addressed by handles with
 * create/destroy and slot reuse across generations, and a frame built by replaying
 * a short, ordered sequence of byte-level draw operations into a target texture.
 *
 * Every value is a fixed-width unsigned type and every byte is written explicitly,
 * so the frame - and therefore the replay that must reproduce it - does not depend
 * on the compiler or the host word size.
 *
 * This library is the *subject*, and it is deliberately independent of the P4
 * recorder: P4b replays a sealed capture of it without linking the recorder, without
 * the original process and without re-running this code. Nothing here launches
 * EU IV or touches a GPU.
 */
#ifndef EU4_ORACLE_REPLAY_SYNTHETIC_GFX_H
#define EU4_ORACLE_REPLAY_SYNTHETIC_GFX_H

#include <stdint.h>

#define GFX_SLOTS 4u
#define GFX_CAPACITY 32u

/* Resource kinds. Only a texture can be a framebuffer target. */
enum {
    GFX_TEXTURE = 1,
    GFX_VERTEX_BUFFER = 2
};

/* Result codes. */
enum {
    GFX_OK = 0,
    GFX_STALE = 1,    /* the handle's generation no longer lives in its slot */
    GFX_NO_SLOT = 2,  /* no free slot */
    GFX_BAD_ARG = 3,  /* size, offset, span or rounds out of range */
    GFX_TYPE = 4      /* the resource kind does not fit the operation */
};

/* Ordered external-effect operation codes. */
enum {
    GFX_EFFECT_CREATE = 1,
    GFX_EFFECT_FILL = 2,
    GFX_EFFECT_BLIT = 3,
    GFX_EFFECT_MIX = 4,
    GFX_EFFECT_DESTROY = 5,
    GFX_EFFECT_PRESENT = 6
};

typedef struct {
    uint32_t slot;
    uint32_t generation;
} gfx_handle;

typedef struct {
    uint32_t live;
    uint32_t kind;
    uint32_t generation;
    uint32_t version;
    uint32_t size;
    uint8_t  data[GFX_CAPACITY];
} gfx_slot;

typedef struct {
    uint32_t creates;
    uint32_t destroys;
    uint32_t ops;
    gfx_slot slots[GFX_SLOTS];
} gfx_world;

/* Trusted effect sink, implemented by the host. Called once per observable
 * operation, in order. */
void gfx_effect(uint32_t op, uint32_t a, uint32_t b);

void gfx_world_init(gfx_world *w);

/* Create a resource of `size` bytes initialised from `contents` (which must have at
 * least `size` bytes). Writes its handle to `out`. */
int gfx_create(gfx_world *w, uint32_t kind, uint32_t size, const uint8_t *contents,
               gfx_handle *out);

/* Destroy the resource. The slot's generation advances, invalidating old handles. */
int gfx_destroy(gfx_world *w, gfx_handle handle);

/* Fill `span` bytes of the target from `offset` with a running pattern. */
int gfx_fill(gfx_world *w, gfx_handle target, uint32_t offset, uint32_t span, uint32_t pattern);

/* XOR the source's bytes into the target from `offset`. */
int gfx_blit(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source);

/* Add the source's bytes into the target from `offset`, `rounds` times. */
int gfx_mix(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source,
            uint32_t rounds);

/* The frame: an order-independent digest of the target's bytes. */
int gfx_present(const gfx_world *w, gfx_handle target, uint32_t *digest_out);

/* FNV-1a over a resource's live bytes; the one hash both sides must agree on. */
uint32_t gfx_digest_bytes(const uint8_t *data, uint32_t size);

#endif /* EU4_ORACLE_REPLAY_SYNTHETIC_GFX_H */
