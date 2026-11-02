#ifndef EU4_GFX_SHADER_H
#define EU4_GFX_SHADER_H

#ifdef __cplusplus
extern "C" {
#endif

/* _GfxCreateShader at 0x15eae97. Arguments are unread: edi is overwritten with 0x78.
   Returns the operator-new(0x78) object. This is the game's 0x78 object, not a
   compiled shader or a Metal pipeline. The qword at +4 is 0. The program name at +0
   stays 0: the original store is the result of callq *(%rax) at 0x15eaeb9, and that
   qword is ___glewCreateProgramObjectARB. A nonzero value would be a fabricated GL name. */
void *eu4_gfx_create_shader(void);

/* _GfxAddVertexShader at 0x15eafae. rdi is unread, rsi is the 0x78 object, and rdx is
   the source pointer. Returns 0 and writes nothing. That is the failure return, not a
   compiled shader. A nonzero dword at +4 is left unchanged; the original detaches it
   first, and that path is not reconstructed. _GfxBuildAndAddVertexShader returns this
   value at 0x15eb3f6. CreateEffectFromFileNew tests AL at 0x1511eba. AL == 0 logs and
   falls through to call _GfxBuildAndAddPixelShader at 0x1511f18. */
int eu4_gfx_add_vertex_shader(void *unused, void *shader, const char *source);

/* _GfxAddPixelShader at 0x15eb425. rdi is unread, rsi is the 0x78 object, and rdx is
   the source pointer. Returns 0 and writes nothing. The dword at +8 is the pixel-shader
   name. A nonzero value is left unchanged; the original detaches it first, and that
   path is not reconstructed. _GfxBuildAndAddPixelShader saves AL at 0x15eb844 and
   returns it. CreateEffectFromFileNew tests AL at 0x1511f1d. AL == 0 logs and falls
   through to call _GfxLinkShader at 0x1511f71. */
int eu4_gfx_add_pixel_shader(void *unused, void *shader, const char *source);

#ifdef __cplusplus
}
#endif

#endif
