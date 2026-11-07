#ifndef EU4_GFX_SHADER_H
#define EU4_GFX_SHADER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Engine object size. Metal ownership lives in the backend registry, not in
   +0/+4/+8 (those remain non-GL, non-Metal sentinel zeros on the Metal path). */
#define EU4_GFX_SHADER_OBJECT_SIZE 0x78

/* Transitional markers for offline lifecycle hosts only. Production Add*
   admission prefers exact SHA-256 of qualified compiler inputs
   (analysis/gfx-shader/v0/qualified-sources.json). Substring matching of
   MainCode fragments is not an engine-path completion criterion. */
#define EU4_GFX_SHADER_SIMPLE_VS_MARKER "EU4_METAL_SIMPLE_VS_V0"
#define EU4_GFX_SHADER_SIMPLE_PS_MARKER "EU4_METAL_SIMPLE_PS_V0"

/* Max number of exact source hashes admitted for Simple (VS+PS variants). */
#define EU4_GFX_SHADER_MAX_QUALIFIED_HASHES 8

/* Register an exact qualified compiler-input SHA-256 (32 bytes). Returns 1 on
   success. Unknown hashes remain rejected. */
int eu4_gfx_shader_register_qualified_sha256(const uint8_t digest[32]);

/* Clear qualified-hash table (tests). */
void eu4_gfx_shader_clear_qualified_hashes(void);

/* Opt into transitional MainCode/marker admission (offline hosts only).
   Default is off; engine-path completion must keep this disabled. */
void eu4_gfx_shader_set_allow_transitional_markers(int allowed);

/* 1 if master+0x70 still points at this object (stale-current detection). */
int eu4_gfx_shader_is_current(const void *immediate_context, const void *shader);

/* Pipeline cache key participants (effective Metal state). */
typedef struct EU4GfxPipelineKey {
    void *vertex_function;
    void *fragment_function;
    unsigned pixel_format; /* MTLPixelFormat as unsigned */
    unsigned sample_count;
    unsigned color_write_mask;
    unsigned blend_enabled;
} EU4GfxPipelineKey;

void *eu4_gfx_shader_pipeline_for_key(const void *shader, void *mtl_device,
                                      const EU4GfxPipelineKey *key);

/* _GfxCreateShader at 0x15eae97. Allocates 0x78 bytes; +0/+4 qword cleared. */
void *eu4_gfx_create_shader(void);

/* _GfxAddVertexShader at 0x15eafae. Success (nonzero) only after a supported
   stage actually compiles into the registry entry for this engine object. */
int eu4_gfx_add_vertex_shader(void *unused, void *shader, const char *source);

/* _GfxAddPixelShader at 0x15eb425. Same success rule as the vertex add. */
int eu4_gfx_add_pixel_shader(void *unused, void *shader, const char *source);

/* _GfxLinkShader at 0x15eb884. Logical link: both stages present and interface
   compatible. Does not create MTLRenderPipelineState. */
int eu4_gfx_link_shader(void *unused, void *shader);

/* _GfxSetShader at 0x15ebbb0. rdi is the immediate context; (%rdi) is master.
   rsi is the shader object (nullable: null returns without clearing current). */
void eu4_gfx_set_shader(void *immediate_context, void *shader);

/* _GfxDestroyShader at 0x15ebb1c. Unregisters Metal resources then deletes. */
void eu4_gfx_destroy_shader(void *shader);

/* Offline / test helpers. Not engine entry points. */
uint64_t eu4_gfx_shader_lifetime_id(const void *shader);
int eu4_gfx_shader_is_linked(const void *shader);
const char *eu4_gfx_shader_identity(const void *shader);
void *eu4_gfx_shader_pipeline(const void *shader, void *mtl_device);
void eu4_gfx_shader_test_reset(void);

/* Reconstructed Simple stage sources used as the asset-maincode-v0 compiler
   input identity when BuildGLSL bytes are not recovered. */
const char *eu4_gfx_simple_vertex_source(void);
const char *eu4_gfx_simple_pixel_source(void);

#ifdef __cplusplus
}
#endif

#endif
