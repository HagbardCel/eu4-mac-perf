#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/gl.h>

extern int g_b2_smoke_count;
extern int g_b2_smoke_base_vertex;
extern int eu4_b2_capture_smoke(const char *dir, int (*issue_draw)(void));

extern void glDrawElementsBaseVertex(GLenum mode, GLsizei count, GLenum type, const void *indices, GLint basevertex);

static int issue(void) {
    if (g_b2_smoke_base_vertex) {
        glDrawElementsBaseVertex(GL_TRIANGLES, g_b2_smoke_count, GL_UNSIGNED_SHORT, (void *)0, 1);
        return 0;
    }
    glDrawElements(GL_TRIANGLES, g_b2_smoke_count, GL_UNSIGNED_SHORT, (void *)0);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    return eu4_b2_capture_smoke(argv[1], issue);
}
