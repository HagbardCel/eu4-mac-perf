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

#ifdef __cplusplus
}
#endif

#endif
