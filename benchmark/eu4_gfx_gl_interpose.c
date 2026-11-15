/* Interposes glCopyTexSubImage2D for the original-GL observer.

   The engine's _GfxResolve is glCopyTexSubImage2D into the composite's sampled
   texture; the source is whatever framebuffer is bound as READ at that moment.
   The engine binds it outside the replaced _Gfx* entries, so this interposer
   records the effective READ/DRAW framebuffer bindings, read buffer, source
   attachment and destination level/extent for every copy.  Evidence only; the
   copy itself is forwarded unchanged. */

#define GL_SILENCE_DEPRECATION 1

#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_observe_framebuffer.h"

typedef void (*copy_fn)(GLenum, GLint, GLint, GLint, GLint, GLint, GLsizei,
                        GLsizei);

static copy_fn g_real;
static uint64_t g_sequence;

/* Bounded, opt-in destination-filtered copy tracer.  It is the only mode that
   records before the frame trace is configured; inactive by default. */
static int g_diag_initialized;
static int g_diag_destination = -1;
static int g_diag_limit;
static int g_diag_count;
static int g_diag_complete_logged;
static char g_diag_path[1024];

static void diag_init(void) {
    if (g_diag_initialized) {
        return;
    }
    g_diag_initialized = 1;
    const char *destination = getenv("EU4_GFX_COPY_TRACE_DEST");
    const char *path = getenv("EU4_GFX_COPY_TRACE_PATH");
    const char *limit = getenv("EU4_GFX_COPY_TRACE_LIMIT");
    if (destination == NULL || path == NULL || path[0] != '/') {
        return;
    }
    char *end = NULL;
    long value = strtol(destination, &end, 0);
    if (end == destination || *end != 0 || value < 0) {
        return;
    }
    g_diag_destination = (int)value;
    snprintf(g_diag_path, sizeof(g_diag_path), "%s", path);
    g_diag_limit = 4096;
    if (limit != NULL) {
        long parsed = strtol(limit, &end, 0);
        if (end != limit && *end == 0 && parsed > 0) {
            g_diag_limit = (int)parsed;
        }
    }
}

static void diag_write(const char *text) {
    if (g_diag_path[0] == 0 || text == NULL) {
        return;
    }
    FILE *file = fopen(g_diag_path, "ab");
    if (file == NULL) {
        return;
    }
    fputs(text, file);
    fclose(file);
}

static void eu4_gfx_interposed_copy_tex_sub_image(GLenum target, GLint level,
                                                  GLint xoffset, GLint yoffset,
                                                  GLint x, GLint y,
                                                  GLsizei width, GLsizei height);

/* Resolve the framework implementation, never this interposer.  RTLD_NEXT can
   return the replacement itself on macOS when the symbol is interposed, which
   would recurse and stall the engine's RenderPostEffects. */
static void *g_opengl_handle;

typedef void *(*interpose_resolver)(const char *name);

/* Shared resolution used by production and the native forwarding tests.  Self
   results are rejected and, when requested, reported through saw_self. */
void *eu4_gfx_interpose_resolve(interpose_resolver next,
                                interpose_resolver framework,
                                int *saw_self) {
    if (saw_self != NULL) {
        *saw_self = 0;
    }
    void *candidate = next != NULL ? next("glCopyTexSubImage2D") : NULL;
    if (candidate == (void *)eu4_gfx_interposed_copy_tex_sub_image) {
        if (saw_self != NULL) {
            *saw_self = 1;
        }
        candidate = NULL;
    }
    if (candidate != NULL) {
        return candidate;
    }
    candidate = framework != NULL ? framework("glCopyTexSubImage2D") : NULL;
    if (candidate == (void *)eu4_gfx_interposed_copy_tex_sub_image) {
        return NULL;
    }
    return candidate;
}

static void *resolve_next(const char *name) {
    return dlsym(RTLD_NEXT, name);
}

static void *resolve_framework(const char *name) {
    if (g_opengl_handle == NULL) {
        g_opengl_handle = dlopen(
            "/System/Library/Frameworks/OpenGL.framework/OpenGL",
            RTLD_LAZY | RTLD_LOCAL | RTLD_FIRST);
    }
    if (g_opengl_handle == NULL) {
        return NULL;
    }
    return dlsym(g_opengl_handle, name);
}

static interpose_resolver g_next_resolver = resolve_next;
static interpose_resolver g_framework_resolver = resolve_framework;

static void *interpose_replacee(void);
#ifdef EU4_GFX_INTERPOSE_TEST
static int g_test_ignore_replacee;
#endif

static copy_fn resolve_real_copy(void) {
    /* The interpose table's replacee is the original binding dyld resolved
       before installing this replacement.  It is the most reliable source
       inside an inserted process, where dlsym(RTLD_NEXT) may return this
       replacement itself. */
    void *replacee = interpose_replacee();
#ifdef EU4_GFX_INTERPOSE_TEST
    if (g_test_ignore_replacee) {
        replacee = NULL;
    }
#endif
    if (replacee != NULL &&
        replacee != (void *)eu4_gfx_interposed_copy_tex_sub_image) {
        return (copy_fn)replacee;
    }
    return (copy_fn)eu4_gfx_interpose_resolve(g_next_resolver,
                                              g_framework_resolver, NULL);
}

/* A passive observer must never silently delete the operation it cannot
   forward.  Fail loudly and terminally instead, with a durable marker when a
   trace directory is configured. */
static void interpose_fatal(const char *message) {
    fprintf(stderr, "EU4 gfx interposer: %s\n", message);
    const char *dir = getenv("EU4_GFX_FRAME_TRACE_DIR");
    if (dir != NULL && dir[0] != 0) {
        char path[1024];
        if (snprintf(path, sizeof path, "%s/interpose-error.jsonl", dir) > 0) {
            FILE *file = fopen(path, "ab");
            if (file != NULL) {
                fprintf(file,
                        "{\"kind\":\"interpose_fatal\",\"pid\":%d,"
                        "\"reason\":\"%s\"}\n",
                        (int)getpid(), message);
                fclose(file);
            }
        }
    }
    fflush(stderr);
    abort();
}

static uint64_t monotonic_ns(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000000000ull + (uint64_t)spec.tv_nsec;
}

static void write_line(const char *text) {
    const char *dir = getenv("EU4_GFX_FRAME_TRACE_DIR");
    if (dir == NULL || dir[0] == 0 || text == NULL) {
        return;
    }
    char path[1024];
    if (snprintf(path, sizeof path, "%s/copy-tex-sub.jsonl", dir) <= 0) {
        return;
    }
    FILE *file = fopen(path, "ab");
    if (file == NULL) {
        return;
    }
    fputs(text, file);
    fclose(file);
}

static void eu4_gfx_interposed_copy_tex_sub_image(GLenum target, GLint level,
                                                  GLint xoffset, GLint yoffset,
                                                  GLint x, GLint y,
                                                  GLsizei width, GLsizei height) {
    if (g_real == NULL) {
        g_real = resolve_real_copy();
        if (g_real == NULL) {
            interpose_fatal("cannot resolve the original glCopyTexSubImage2D");
        }
    }
    diag_init();
    int record = eu4_gfx_frame_trace_copy_mode();
    int diag_record = 0;
    if (!record && g_diag_destination >= 0 &&
        g_diag_count < g_diag_limit) {
        GLint bound = 0;
        glGetIntegerv(0x8069, &bound); /* GL_TEXTURE_BINDING_2D */
        if (bound == g_diag_destination) {
            diag_record = 1;
            g_diag_count++;
        }
    }
    if (!record && !diag_record) {
        g_real(target, level, xoffset, yoffset, x, y, width, height);
        return;
    }
    GLint read_fbo = 0, draw_fbo = 0, fbo = 0;
    GLint read_buffer = 0, draw_buffer = 0, double_buffered = 0;
    GLint bound_texture = 0;
    glGetIntegerv(0x8CAA, &read_fbo); /* GL_READ_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8CA6, &draw_fbo); /* GL_DRAW_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8D40, &fbo);      /* GL_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x0C02, &read_buffer); /* GL_READ_BUFFER */
    glGetIntegerv(0x0C01, &draw_buffer); /* GL_DRAW_BUFFER */
    glGetIntegerv(0x0C32, &double_buffered); /* GL_DOUBLEBUFFER */
    glGetIntegerv(0x8069, &bound_texture);   /* GL_TEXTURE_BINDING_2D */
    GLint color_type = 0, color_name = 0, color_level = 0;
    GLint source_width = 0, source_height = 0, source_format = 0;
    GLint source_attachment = 0;
    if (read_fbo != 0) {
        /* Query the attachment selected by GL_READ_BUFFER, not attachment
           zero: a non-default resolve may read another color attachment. */
        if (read_buffer >= 0x8CE0 && read_buffer <= 0x8CEF) {
            source_attachment = read_buffer;
            /* The read framebuffer is already bound; query it directly so no
               framebuffer binding is changed. */
            glGetFramebufferAttachmentParameteriv(
                0x8CA8, (GLenum)source_attachment, 0x8CD0, &color_type);
            glGetFramebufferAttachmentParameteriv(
                0x8CA8, (GLenum)source_attachment, 0x8CD1, &color_name);
            if (color_type == 0x1702) { /* GL_TEXTURE */
                GLint cube_face = 0, layer = 0;
                glGetFramebufferAttachmentParameteriv(
                    0x8CA8, (GLenum)source_attachment, 0x8CD2, &color_level);
                /* Only plain 2D texture attachments are supported.  A cube
                   face or layered attachment cannot be inspected through the
                   currently bound GL_TEXTURE_2D without guessing. */
                glGetFramebufferAttachmentParameteriv(
                    0x8CA8, (GLenum)source_attachment, 0x8CD3, &cube_face);
                glGetFramebufferAttachmentParameteriv(
                    0x8CA8, (GLenum)source_attachment, 0x8CD4, &layer);
                if (cube_face == 0 && layer == 0 && color_name != 0) {
                    GLint previous_active = 0;
                    GLint previous_texture = 0;
                    glGetIntegerv(0x84E0, &previous_active); /* ACTIVE_TEXTURE */
                    glGetIntegerv(0x8069, &previous_texture); /* BINDING_2D */
                    glBindTexture(GL_TEXTURE_2D, (GLuint)color_name);
                    glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                             GL_TEXTURE_WIDTH, &source_width);
                    glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                             GL_TEXTURE_HEIGHT, &source_height);
                    glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                             GL_TEXTURE_INTERNAL_FORMAT,
                                             &source_format);
                    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
                    glActiveTexture((GLenum)previous_active);
                } else {
                    color_type = 0; /* unsupported attachment variant */
                }
            }
        }
    } else {
        uint32_t extent_width = 0, extent_height = 0;
        if (eu4_gfx_gl_drawable_extent(&extent_width, &extent_height) == 0 &&
            extent_width > 0 && extent_height > 0) {
            source_width = (GLint)extent_width;
            source_height = (GLint)extent_height;
        } else {
            GLint viewport[4] = {0, 0, 0, 0};
            glGetIntegerv(GL_VIEWPORT, viewport);
            source_width = viewport[2] > 0 ? viewport[2] : (GLint)width;
            source_height = viewport[3] > 0 ? viewport[3] : (GLint)height;
        }
    }
    GLint destination_width = 0, destination_height = 0;
    GLint destination_format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH,
                             &destination_width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT,
                             &destination_height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_INTERNAL_FORMAT,
                             &destination_format);
    eu4_gfx_copy_event event;
    memset(&event, 0, sizeof(event));
    event.is_blit = 0;
    event.source_framebuffer = read_fbo;
    event.read_buffer = read_buffer;
    event.draw_buffer = draw_buffer;
    event.double_buffered = double_buffered;
    event.source_color_type = color_type;
    event.source_color_name = color_name;
    event.source_color_level = color_level;
    event.source_attachment = source_attachment;
    event.source_texture = color_name;
    event.source_level = color_level;
    event.source_width = source_width;
    event.source_height = source_height;
    event.source_internal_format = source_format;
    event.source_resolved =
        read_fbo == 0 ? (read_buffer == 0x0405)
                      : (source_attachment != 0 && color_type == 0x1702 &&
                         color_name != 0);
    event.destination_resolved =
        target == GL_TEXTURE_2D && bound_texture != 0 && level == 0;
    event.destination_texture = bound_texture;
    event.destination_level = level;
    event.destination_width = destination_width;
    event.destination_height = destination_height;
    event.destination_internal_format = destination_format;
    event.x = x;
    event.y = y;
    event.width = width;
    event.height = height;
    event.dx = xoffset;
    event.dy = yoffset;
    char body[640];
    uint64_t sequence = ++g_sequence;
    int length = snprintf(
        body, sizeof body,
        "{\"seq\":%llu,\"pid\":%d,\"monotonic_ns\":%llu,"
        "\"read_fbo\":%d,\"draw_fbo\":%d,\"fbo\":%d,"
        "\"read_buffer\":%d,\"draw_buffer\":%d,\"double_buffered\":%d,"
        "\"color_type\":%d,\"color_name\":%d,\"color_level\":%d,"
        "\"source_attachment\":%d,\"source_width\":%d,\"source_height\":%d,\"source_format\":%d,"
        "\"source_resolved\":%d,\"texture\":%d,\"level\":%d,"
        "\"destination_width\":%d,\"destination_height\":%d,"
        "\"destination_format\":%d,"
        "\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d,"
        "\"xoffset\":%d,\"yoffset\":%d}\n",
        (unsigned long long)sequence, (int)getpid(),
        (unsigned long long)monotonic_ns(), read_fbo, draw_fbo, fbo,
        read_buffer, draw_buffer, double_buffered, color_type, color_name,
        color_level, source_attachment, source_width, source_height,
        source_format,
        event.source_resolved, bound_texture, level, destination_width,
        destination_height, destination_format, x, y, width, height, xoffset,
        yoffset);
    if (length > 0 && (size_t)length < sizeof body) {
        write_line(body);
    }
    if (diag_record) {
        char diag[768];
        int diag_length = snprintf(
            diag, sizeof diag,
            "{\"kind\":\"copy\",\"seq\":%llu,\"pid\":%d,"
            "\"monotonic_ns\":%llu,\"destination\":%d,"
            "\"source_framebuffer\":%d,\"read_buffer\":%d,"
            "\"source_attachment\":%d,\"source_resolved\":%d,"
            "\"source_rect\":[%d,%d,%d,%d],"
            "\"destination_offset\":[%d,%d],"
            "\"source_width\":%d,\"source_height\":%d,"
            "\"destination_width\":%d,\"destination_height\":%d,"
            "\"source_format\":%d,\"destination_format\":%d}\n",
            (unsigned long long)sequence, (int)getpid(),
            (unsigned long long)monotonic_ns(), event.destination_texture,
            event.source_framebuffer, event.read_buffer,
            event.source_attachment, event.source_resolved, event.x, event.y,
            event.width, event.height, event.dx, event.dy, event.source_width,
            event.source_height, event.destination_width,
            event.destination_height, event.source_internal_format,
            event.destination_internal_format);
        if (diag_length > 0 && (size_t)diag_length < sizeof diag) {
            diag_write(diag);
        }
        if (g_diag_count >= g_diag_limit && !g_diag_complete_logged) {
            g_diag_complete_logged = 1;
            char done[128];
            int done_length = snprintf(
                done, sizeof done,
                "{\"kind\":\"copy_trace_complete\",\"count\":%d}\n",
                g_diag_count);
            if (done_length > 0 && (size_t)done_length < sizeof done) {
                diag_write(done);
            }
        }
    }
    (void)eu4_gfx_frame_trace_copy_event(&event);
    g_real(target, level, xoffset, yoffset, x, y, width, height);
}

#ifdef EU4_GFX_INTERPOSE_TEST
void eu4_gfx_interpose_test_set_resolvers(interpose_resolver next,
                                          interpose_resolver framework) {
    g_next_resolver = next;
    g_framework_resolver = framework;
    g_real = NULL;
}

void eu4_gfx_interpose_test_ignore_replacee(int ignore) {
    g_test_ignore_replacee = ignore;
}

void *eu4_gfx_interpose_test_replacee(void) {
    return interpose_replacee();
}

void eu4_gfx_interpose_test_reset(void) {
    g_next_resolver = resolve_next;
    g_framework_resolver = resolve_framework;
    g_real = NULL;
}

void *eu4_gfx_interpose_test_self(void) {
    return (void *)eu4_gfx_interposed_copy_tex_sub_image;
}

void eu4_gfx_interpose_test_invoke(GLenum target, GLint level, GLint xoffset,
                                   GLint yoffset, GLint x, GLint y,
                                   GLsizei width, GLsizei height) {
    eu4_gfx_interposed_copy_tex_sub_image(target, level, xoffset, yoffset, x, y,
                                         width, height);
}
#endif

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} eu4_gfx_copy_interpose __attribute__((section("__DATA,__interpose"))) = {
    (const void *)eu4_gfx_interposed_copy_tex_sub_image,
    (const void *)glCopyTexSubImage2D,
};

static void *interpose_replacee(void) {
    return (void *)eu4_gfx_copy_interpose.replacee;
}
