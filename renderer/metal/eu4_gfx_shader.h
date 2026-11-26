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

/* The registry identity assigned to the admitted Simple effect
   (asset-maincode-v0). Admission compares this exact string; it is not a
   prefix or substring match. */
#define EU4_GFX_SHADER_SIMPLE_IDENTITY "simple-lua-Simple/asset-maincode-v0"
/* The loading-screen sprite/font effect reached after the Simple text draw.
   VS: UNIFORM0[6] (matrix, Color, vXOffset), attributes float3 position +
   float2 texcoord. PS: MapTexture sampled and multiplied by Color. */
#define EU4_GFX_SHADER_SPRITE_IDENTITY "sprite-map/asset-maincode-v0"

/* Vertex layouts admitted by the pipeline constructor. The layout is part of
   the effective state, not a caller property. */
enum {
    EU4_GFX_VERTEX_LAYOUT_SIMPLE = 0, /* stride 24: float3, float2, uchar4 */
    EU4_GFX_VERTEX_LAYOUT_SPRITE_MAP = 1, /* stride 20: float3, float2 */
};

/* Max number of exact source hashes admitted for Simple (VS+PS variants). */
#define EU4_GFX_SHADER_MAX_QUALIFIED_HASHES 8

/* Shader stage identity for exact source-hash admission. */
typedef enum {
    EU4_GFX_SHADER_STAGE_VERTEX = 1,
    EU4_GFX_SHADER_STAGE_PIXEL = 2,
} eu4_gfx_shader_stage;

/* Register an exact qualified compiler-input SHA-256 for one stage and one
   effect. Returns 1 on success. A vertex hash never admits a pixel source
   (and vice versa). Unknown hashes remain rejected. */
int eu4_gfx_shader_register_qualified_sha256(int stage, const uint8_t digest[32],
                                             const char *effect_identity);

/* Number of active qualified source records (tests/diagnostics). */
size_t eu4_gfx_shader_qualified_count(void);

/* Clear qualified-hash table (tests). */
void eu4_gfx_shader_clear_qualified_hashes(void);

/* Runtime shader cache (gfx-shader-cache/v1) produced by
   automation/gfx_shader_translate.py. Stages are admitted by the exact
   SHA-256 of the engine's compiler input; the cache carries the MSL file,
   entry point, attribute/varying locations, std140 uniform layout and sampler
   bindings. Returns the number of cached stages, or -1 on an unreadable file. */
int eu4_gfx_shader_load_cache(const char *path);
size_t eu4_gfx_shader_cache_count(void);

/* 1 when this engine shader object was admitted from the runtime cache. */
int eu4_gfx_shader_is_generic(const void *shader);

/* Borrowed metadata dictionaries for the admitted stages (nil when absent).
   Foundation objects; ObjC++ callers only. */
void *eu4_gfx_shader_vertex_stage(const void *shader);
void *eu4_gfx_shader_pixel_stage(const void *shader);
const char *eu4_gfx_shader_cache_dir(void);

/* Lazy MSL compilation for the generic path. Returns 1 and sets both
   functions. */
int eu4_gfx_shader_generic_functions(const void *shader, void *device,
                                     void **vertex_function,
                                     void **fragment_function);

/* Load exact compiler-input SHA-256 values from the tracked qualification
   manifest (analysis/gfx-shader/v0/qualified-sources.json). Returns the number
   registered, or -1 on an unreadable/malformed manifest. This never enables
   transitional marker admission. */
int eu4_gfx_shader_load_qualified_sources(const char *path);

/* Opt into transitional MainCode/marker admission (offline hosts only).
   Default is off; engine-path completion must keep this disabled. */
void eu4_gfx_shader_set_allow_transitional_markers(int allowed);

/* 1 if master+0x70 still points at this object (stale-current detection). */
int eu4_gfx_shader_is_current(const void *immediate_context, const void *shader);

/* Pipeline cache key participants (effective Metal state).
   mtl_device is required in the key so PSOs never cross devices. */
typedef struct EU4GfxPipelineKey {
    void *mtl_device;
    void *vertex_function;
    void *fragment_function;
    unsigned pixel_format; /* MTLPixelFormat as unsigned */
    unsigned sample_count;
    unsigned color_write_mask;
    unsigned blend_enabled;
    unsigned vertex_layout; /* EU4_GFX_VERTEX_LAYOUT_* */
} EU4GfxPipelineKey;

void *eu4_gfx_shader_pipeline_for_key(const void *shader, void *mtl_device,
                                      const EU4GfxPipelineKey *key);

/* Reflect linked Metal stages; 1 if VS/PS functions expose the expected
   Simple interface (production validation, not capture/replay). */
int eu4_gfx_shader_reflect_validate(const void *shader);

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

/* Offline/test only: relabel one registry record so admission logic can be
   exercised with a linked shader that is not the Simple effect. This does not
   admit a new stage source or widen the Simple registry. */
int eu4_gfx_shader_test_set_identity(void *shader, const char *identity);

/* Offline/test only: compile the known Metal library for `identity` into an
   existing engine shader object and mark both stages linked. Used by the
   deterministic replay host; the engine path still requires exact source
   admission. */
int eu4_gfx_shader_test_compile_identity(void *shader, const char *identity);

/* Capture / GPU-trace helpers are diagnostic-only and must not gate admission. */

/* Reconstructed Simple stage sources used as the asset-maincode-v0 compiler
   input identity when BuildGLSL bytes are not recovered. */
const char *eu4_gfx_simple_vertex_source(void);
const char *eu4_gfx_simple_pixel_source(void);

#ifdef __cplusplus
}
#endif

#endif
