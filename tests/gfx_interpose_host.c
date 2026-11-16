/* Native forwarding/resolution regressions for the glCopyTexSubImage2D
   interposer.  The production interposer is compiled with
   -DEU4_GFX_INTERPOSE_TEST and linked here; no OpenGL context is required. */
#define GL_SILENCE_DEPRECATION 1

#include "eu4_gfx_frame_trace.h"

#include <OpenGL/gl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

/* The interposer only calls these when an operation is recorded.  These tests
   exercise the disabled/first-resolution path, so they must remain untouched. */
int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) *width = 4;
    if (height != NULL) *height = 4;
    return 0;
}

int eu4_gfx_frame_trace_copy_mode(void) { return 0; }
void eu4_gfx_frame_trace_texture_deleted(int count, const unsigned *names) {
    (void)count;
    (void)names;
}
int eu4_gfx_frame_trace_copy_event(const eu4_gfx_copy_event *event) {
    (void)event;
    return 0;
}

void *eu4_gfx_interpose_resolve(void *(*next)(const char *),
                                void *(*framework)(const char *),
                                int *saw_self);
void eu4_gfx_interpose_test_set_resolvers(void *(*next)(const char *),
                                          void *(*framework)(const char *));
void eu4_gfx_interpose_test_reset(void);
void eu4_gfx_interpose_test_ignore_replacee(int ignore);
void *eu4_gfx_interpose_test_replacee(void);
void *eu4_gfx_interpose_test_self(void);
void eu4_gfx_interpose_test_invoke(GLenum target, GLint level, GLint xoffset,
                                   GLint yoffset, GLint x, GLint y,
                                   GLsizei width, GLsizei height);

static int g_calls;
static GLenum g_target;
static GLint g_level, g_xoffset, g_yoffset, g_x, g_y;
static GLsizei g_width, g_height;

static void fake_original(GLenum target, GLint level, GLint xoffset,
                          GLint yoffset, GLint x, GLint y, GLsizei width,
                          GLsizei height) {
    g_calls++;
    g_target = target;
    g_level = level;
    g_xoffset = xoffset;
    g_yoffset = yoffset;
    g_x = x;
    g_y = y;
    g_width = width;
    g_height = height;
}

static void *resolver_original(const char *name) {
    (void)name;
    return (void *)fake_original;
}

static void *resolver_self(const char *name) {
    (void)name;
    return eu4_gfx_interpose_test_self();
}

static void *resolver_missing(const char *name) {
    (void)name;
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <dir>\n", argv[0]);
        return 2;
    }
    int ok = 1;
    int saw_self = 0;
    if (eu4_gfx_interpose_test_replacee() == NULL) {
        fprintf(stderr, "interpose replacee is unavailable\n");
        ok = 0;
    }
    eu4_gfx_interpose_test_ignore_replacee(1);

    if (eu4_gfx_interpose_resolve(resolver_original, resolver_missing,
                                  &saw_self) != (void *)fake_original ||
        saw_self != 0) {
        fprintf(stderr, "successful resolution failed\n");
        ok = 0;
    }

    saw_self = 0;
    if (eu4_gfx_interpose_resolve(resolver_missing, resolver_original,
                                  &saw_self) != (void *)fake_original ||
        saw_self != 0) {
        fprintf(stderr, "framework fallback failed\n");
        ok = 0;
    }

    /* A resolver that returns the replacement itself must be rejected and the
       framework fallback used. */
    saw_self = 0;
    void *from_self = eu4_gfx_interpose_resolve(
        resolver_self, resolver_original, &saw_self);
    if (from_self != (void *)fake_original || saw_self != 1) {
        fprintf(stderr, "self resolution was not rejected\n");
        ok = 0;
    }

    saw_self = 0;
    if (eu4_gfx_interpose_resolve(resolver_self, resolver_missing,
                                  &saw_self) != NULL ||
        saw_self != 1) {
        fprintf(stderr, "self-only resolution was not rejected\n");
        ok = 0;
    }

    /* Missing original: the child must abort, not silently skip the copy. */
    setenv("EU4_GFX_FRAME_TRACE_DIR", argv[1], 1);
    pid_t child = fork();
    if (child == 0) {
        eu4_gfx_interpose_test_set_resolvers(resolver_missing,
                                             resolver_missing);
        eu4_gfx_interpose_test_invoke(GL_TEXTURE_2D, 0, 0, 0, 0, 0, 4, 4);
        _exit(0);
    }
    int status = 0;
    if (child < 0 || waitpid(child, &status, 0) != child) {
        fprintf(stderr, "fork/wait failed\n");
        return 1;
    }
    if (!WIFSIGNALED(status) || WTERMSIG(status) != SIGABRT) {
        fprintf(stderr, "missing original did not abort (status 0x%x)\n",
                status);
        ok = 0;
    }
    char error_path[1024];
    snprintf(error_path, sizeof(error_path), "%s/interpose-error.jsonl",
             argv[1]);
    FILE *error_file = fopen(error_path, "rb");
    if (error_file == NULL) {
        fprintf(stderr, "missing interpose error marker\n");
        ok = 0;
    } else {
        char error_body[512];
        size_t got = fread(error_body, 1, sizeof(error_body) - 1, error_file);
        fclose(error_file);
        error_body[got] = 0;
        if (strstr(error_body, "interpose_fatal") == NULL ||
            strstr(error_body, "glCopyTexSubImage2D") == NULL) {
            fprintf(stderr, "interpose error marker is not diagnostic\n");
            ok = 0;
        }
    }

    /* Ordinary execution with instrumentation disabled forwards exactly once
       and preserves every argument. */
    eu4_gfx_interpose_test_set_resolvers(resolver_original, resolver_missing);
    g_calls = 0;
    eu4_gfx_interpose_test_invoke(GL_TEXTURE_2D, 0, 3, 5, 1, 2, 6, 7);
    if (g_calls != 1 || g_target != GL_TEXTURE_2D || g_level != 0 ||
        g_xoffset != 3 || g_yoffset != 5 || g_x != 1 || g_y != 2 ||
        g_width != 6 || g_height != 7) {
        fprintf(stderr, "ordinary forwarding changed the call\n");
        ok = 0;
    }
    eu4_gfx_interpose_test_reset();
    printf("interpose_ok=%d calls=%d\n", ok, g_calls);
    return ok ? 0 : 1;
}
