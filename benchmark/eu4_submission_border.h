#ifndef EU4_SUBMISSION_BORDER_H
#define EU4_SUBMISSION_BORDER_H

#include <OpenGL/OpenGL.h>
#include <stdbool.h>
#include <stdint.h>

void eu4_border_init_gl_apis(void);
void eu4_border_publish_tls_counters(void);
void eu4_border_reset_batch(void);
void eu4_border_flush_pending(void);
bool eu4_border_gl_api_ready(void);
bool eu4_border_mutate_enabled(void);
bool eu4_border_minimal_hook(void);

void eu4_border_on_draw_elements_base_vertex(
    GLenum mode,
    GLsizei count,
    GLenum type,
    const void *indices,
    GLint basevertex,
    uint32_t site_id);

#endif
