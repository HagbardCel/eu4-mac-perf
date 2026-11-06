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

/* Identity key for the P5.2a Simple effect asset-maincode reconstruction.
   Cached MSL is keyed by this compiler-input identity, not by effect filename
   alone. */
#define EU4_GFX_SHADER_SIMPLE_VS_MARKER "EU4_METAL_SIMPLE_VS_V0"
#define EU4_GFX_SHADER_SIMPLE_PS_MARKER "EU4_METAL_SIMPLE_PS_V0"

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
