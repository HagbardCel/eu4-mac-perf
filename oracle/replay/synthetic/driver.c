/* Scenario driver for the Oracle P4b synthetic graphics subject.
 *
 * Runs one frame script through the trusted hook layer and prints a canonical
 * capture: the ordered resource timeline (every create, destroy and draw operation,
 * with the referenced lifetimes and, for creates, the initial contents) followed by
 * the presented frame. That transcript is the *whole* input an independent replay is
 * allowed to need - no original process, no recorder, no re-run of the subject.
 *
 * Fixed-width unsigned arithmetic, explicit hex, no pointers, no clock and no
 * locale, so the frame is the same on every compiler and word size.
 *
 * It never launches EU IV, opens a game process, or touches a GPU.
 */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

#include "gfx.h"

enum {
    HOOK_CREATE = 1,
    HOOK_DESTROY = 2,
    HOOK_FILL = 3,
    HOOK_BLIT = 4,
    HOOK_MIX = 5,
    HOOK_PRESENT = 6
};

/* The subject's initial resource contents. They are inputs to the frame, not
 * computed by it, so the capture carries them verbatim. */
static const uint8_t CONTENTS_A[16] = {
    0x00u, 0x01u, 0x02u, 0x03u, 0x04u, 0x05u, 0x06u, 0x07u,
    0x08u, 0x09u, 0x0au, 0x0bu, 0x0cu, 0x0du, 0x0eu, 0x0fu
};
static const uint8_t CONTENTS_B[8] = { 0xa0u, 0xa1u, 0xa2u, 0xa3u, 0xa4u, 0xa5u, 0xa6u, 0xa7u };
static const uint8_t CONTENTS_C[8] = { 0xc0u, 0xc1u, 0xc2u, 0xc3u, 0xc4u, 0xc5u, 0xc6u, 0xc7u };
static const uint8_t CONTENTS_D[8] = { 0xd0u, 0xd1u, 0xd2u, 0xd3u, 0xd4u, 0xd5u, 0xd6u, 0xd7u };

static uint64_t g_effects;

void gfx_effect(uint32_t op, uint32_t a, uint32_t b)
{
    (void)op;
    (void)a;
    (void)b;
    g_effects += 1u;
}

static void print_hex(const uint8_t *data, uint32_t size)
{
    uint32_t i;
    for (i = 0; i < size; i++) {
        printf("%02x", (unsigned)data[i]);
    }
}

/* --- hook layer: the driver owns it, and the replay never sees it ------------- */

static void hook_create(gfx_world *w, uint32_t kind, uint32_t size, const uint8_t *contents,
                        gfx_handle *out)
{
    gfx_handle produced = {0u, 0u};
    int rc = gfx_create(w, kind, size, contents, &produced);
    if (out != 0) {
        *out = produced;
    }
    printf("CREATE slot=%u gen=%u kind=%u size=%u result=%d contents=",
           (unsigned)produced.slot, (unsigned)produced.generation, (unsigned)kind,
           (unsigned)size, rc);
    print_hex(contents, size);
    printf("\n");
}

static void hook_destroy(gfx_world *w, gfx_handle handle)
{
    int rc = gfx_destroy(w, handle);
    printf("DESTROY slot=%u gen=%u result=%d\n",
           (unsigned)handle.slot, (unsigned)handle.generation, rc);
}

static void hook_fill(gfx_world *w, gfx_handle target, uint32_t offset, uint32_t span,
                      uint32_t pattern)
{
    int rc = gfx_fill(w, target, offset, span, pattern);
    printf("FILL slot=%u gen=%u offset=%u span=%u pattern=%u result=%d\n",
           (unsigned)target.slot, (unsigned)target.generation, (unsigned)offset,
           (unsigned)span, (unsigned)pattern, rc);
}

static void hook_blit(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source)
{
    int rc = gfx_blit(w, target, offset, source);
    printf("BLIT slot=%u gen=%u offset=%u source_slot=%u source_gen=%u result=%d\n",
           (unsigned)target.slot, (unsigned)target.generation, (unsigned)offset,
           (unsigned)source.slot, (unsigned)source.generation, rc);
}

static void hook_mix(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source,
                     uint32_t rounds)
{
    int rc = gfx_mix(w, target, offset, source, rounds);
    printf("MIX slot=%u gen=%u offset=%u source_slot=%u source_gen=%u rounds=%u result=%d\n",
           (unsigned)target.slot, (unsigned)target.generation, (unsigned)offset,
           (unsigned)source.slot, (unsigned)source.generation, (unsigned)rounds, rc);
}

static void hook_present(const gfx_world *w, gfx_handle target)
{
    uint32_t digest = 0u;
    int rc = gfx_present(w, target, &digest);
    printf("PRESENT slot=%u gen=%u result=%d digest=%08" PRIx32 " bytes=",
           (unsigned)target.slot, (unsigned)target.generation, rc, digest);
    print_hex(w->slots[target.slot].data, w->slots[target.slot].size);
    printf("\n");
}

/* --- the frame ---------------------------------------------------------------- */

int main(void)
{
    gfx_world world;
    gfx_handle texture = {0u, 0u};
    gfx_handle stage = {0u, 0u};
    gfx_handle scratch = {0u, 0u};
    gfx_handle vertices = {0u, 0u};

    printf("GFX v1\n");
    gfx_world_init(&world);

    hook_create(&world, GFX_TEXTURE, 16u, CONTENTS_A, &texture);
    hook_create(&world, GFX_VERTEX_BUFFER, 8u, CONTENTS_B, &vertices);
    hook_create(&world, GFX_TEXTURE, 8u, CONTENTS_C, &stage);

    hook_fill(&world, texture, 0u, 16u, 3u);
    hook_present(&world, texture);

    hook_blit(&world, texture, 2u, vertices);
    hook_fill(&world, texture, 4u, 8u, 9u);
    hook_mix(&world, texture, 1u, stage, 2u);

    /* Handle reuse: the vertex buffer's slot is reused in a new generation, and an
     * old handle to the destroyed lifetime must never resolve again. */
    hook_destroy(&world, vertices);
    hook_create(&world, GFX_VERTEX_BUFFER, 8u, CONTENTS_D, &scratch);
    hook_blit(&world, texture, 0u, scratch);
    hook_fill(&world, texture, 0u, 4u, 2u);

    hook_present(&world, texture);

    printf("END creates=%u destroys=%u ops=%u effects=%" PRIu64 "\n",
           (unsigned)world.creates, (unsigned)world.destroys, (unsigned)world.ops, g_effects);
    return 0;
}
