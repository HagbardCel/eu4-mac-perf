/* Native capture regression host for texture-content versions.

   A real legacy OpenGL context captures a tiny frame whose only sampled
   texture is first observed as version A, mutated between two draws, and
   sampled again.  The captured event stream must carry A for the first draw
   and the new contents for the second; an unchanged texture must stay cached.
   No Metal, GPU readback or EU4 process is involved.

   Usage: gfx_texture_mutation_host <scenario> <output-directory> */
#define GL_SILENCE_DEPRECATION 1
#define GL_GLEXT_PROTOTYPES 1

#import <AppKit/AppKit.h>
#import <OpenGL/gl.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_observe.h"

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    (void)call;
    (void)kind;
}

int eu4_gfx_frame_observe_init(const char *a, const char *b, const char *c) {
    (void)a;
    (void)b;
    (void)c;
    return 0;
}

int eu4_gfx_frame_observe_wants(uint32_t scene) {
    (void)scene;
    return 0;
}

int eu4_gfx_frame_observe_lockstep_enabled(void) { return 0; }

int eu4_gfx_frame_observe_lockstep_wait(uint32_t scene, uint32_t timeout) {
    (void)scene;
    (void)timeout;
    return 0;
}

int eu4_gfx_frame_observe_emit_metadata(uint32_t a, uint32_t b, const char *c,
                                        uint32_t d, uint32_t e, uint64_t f,
                                        uint32_t g, int h, const char *i) {
    (void)a;
    (void)b;
    (void)c;
    (void)d;
    (void)e;
    (void)f;
    (void)g;
    (void)h;
    (void)i;
    return 0;
}

int eu4_gfx_frame_observe_emit(const uint8_t *a, uint32_t b, uint32_t c,
                               const char *d, uint32_t e, uint32_t f,
                               uint64_t g, uint32_t h, int i, int j,
                               const char *k) {
    (void)a;
    (void)b;
    (void)c;
    (void)d;
    (void)e;
    (void)f;
    (void)g;
    (void)h;
    (void)i;
    (void)j;
    (void)k;
    return 0;
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) {
        *width = 4;
    }
    if (height != NULL) {
        *height = 4;
    }
    return 0;
}

static const char *VERTEX_SOURCE =
    "attribute vec2 pos;\n"
    "varying vec2 uv;\n"
    "void main() { uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }\n";
static const char *FRAGMENT_SOURCE =
    "uniform sampler2D tex0;\n"
    "varying vec2 uv;\n"
    "void main() { gl_FragColor = texture2D(tex0, uv); }\n";

static void fail(const char *what) {
    fprintf(stderr, "texture mutation host: %s\n", what);
    exit(1);
}

static GLuint compile(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024];
        glGetShaderInfoLog(shader, sizeof(log), NULL, log);
        fprintf(stderr, "texture mutation host: shader: %s\n", log);
        exit(1);
    }
    return shader;
}

typedef struct {
    GLuint program;
    GLuint texture;
    GLuint vbo;
    unsigned char version_a[64];
    unsigned char version_b[64];
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    int call_id;
} host_state;

static void issue_draw(host_state *host, int record) {
    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, host->texture);
    glUseProgram(host->program);
    if (record) {
        host->call.call_id = ++host->call_id;
        eu4_gfx_frame_trace_draw_entry(&host->frame, &host->call);
    }
    glDrawArrays(GL_TRIANGLES, 0, 3);
    if (record) {
        eu4_gfx_frame_trace_draw_exit(&host->frame, &host->call);
    }
}

static void boundary(host_state *host) {
    eu4_gfx_frame_trace_boundary_entry(&host->frame, &host->call);
    eu4_gfx_frame_trace_boundary_exit(&host->frame, &host->call, NULL);
}

static void upload_texture(const host_state *host, const unsigned char *pixels,
                           int reallocate, int level) {
    if (reallocate) {
        glTexImage2D(GL_TEXTURE_2D, level, GL_RGBA8, 4, 4, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, pixels);
    } else {
        glTexSubImage2D(GL_TEXTURE_2D, level, 0, 0, 4, 4, GL_RGBA,
                        GL_UNSIGNED_BYTE, pixels);
    }
}

static void mutate(host_state *host, const unsigned char *pixels, int sub_image,
                   int reallocate) {
    if (reallocate) {
        glBindTexture(GL_TEXTURE_2D, host->texture);
    }
    eu4_gfx_frame_trace_texture_mutated(host->texture, 0, sub_image,
                                        GL_TEXTURE_2D);
    upload_texture(host, pixels, reallocate, 0);
}

static void run_scenario(host_state *host, const char *scenario) {
    /* Scene 0 selects the next scene for capture; scene 1 is the captured
       frame and performs the scenario's ordered draws. */
    issue_draw(host, 1);
    boundary(host);
    if (strcmp(scenario, "subimage") == 0) {
        issue_draw(host, 1);
        mutate(host, host->version_b, 1, 0);
        issue_draw(host, 1);
    } else if (strcmp(scenario, "realloc") == 0) {
        issue_draw(host, 1);
        mutate(host, host->version_b, 0, 1);
        issue_draw(host, 1);
    } else if (strcmp(scenario, "name-reuse") == 0) {
        issue_draw(host, 1);
        GLuint name = host->texture;
        eu4_gfx_frame_trace_texture_deleted(1, &name);
        glDeleteTextures(1, &host->texture);
        /* Binding an unused name creates a new texture object.  The numeric
           name is deliberately reused. */
        glBindTexture(GL_TEXTURE_2D, name);
        host->texture = name;
        eu4_gfx_frame_trace_texture_mutated(host->texture, 0, 0,
                                            GL_TEXTURE_2D);
        upload_texture(host, host->version_b, 1, 0);
        issue_draw(host, 1);
    } else if (strcmp(scenario, "presample") == 0) {
        mutate(host, host->version_b, 1, 0);
        issue_draw(host, 1);
    } else if (strcmp(scenario, "unchanged") == 0) {
        issue_draw(host, 1);
        issue_draw(host, 1);
    } else {
        fail("unknown scenario");
    }
    boundary(host);
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <scenario> <output-directory>\n", argv[0]);
        return 2;
    }
    const char *scenario = argv[1];
    const char *directory = argv[2];
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSWindow *window = [[NSWindow alloc]
            initWithContentRect:NSMakeRect(-200, -200, 8, 8)
                      styleMask:NSWindowStyleMaskBorderless
                        backing:NSBackingStoreBuffered
                          defer:NO];
        [window setReleasedWhenClosed:NO];
        NSOpenGLPixelFormatAttribute attributes[] = {
            NSOpenGLPFAColorSize, 24,
            NSOpenGLPFADepthSize, 24,
            NSOpenGLPFADoubleBuffer,
            NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersionLegacy,
            0};
        NSOpenGLPixelFormat *format = [[NSOpenGLPixelFormat alloc]
            initWithAttributes:attributes];
        if (format == nil) {
            fail("no pixel format");
        }
        NSOpenGLContext *context = [[NSOpenGLContext alloc]
            initWithFormat:format
              shareContext:nil];
        if (context == nil) {
            fail("no context");
        }
        [window orderFront:nil];
        [context setView:[window contentView]];
        [context makeCurrentContext];
        glViewport(0, 0, 4, 4);

        host_state host;
        memset(&host, 0, sizeof(host));
        for (int i = 0; i < 64; i++) {
            host.version_a[i] = (unsigned char)(i * 3);
            host.version_b[i] = (unsigned char)(255 - i * 3);
        }
        host.program = glCreateProgram();
        glAttachShader(host.program,
                       compile(GL_VERTEX_SHADER, VERTEX_SOURCE));
        glAttachShader(host.program,
                       compile(GL_FRAGMENT_SHADER, FRAGMENT_SOURCE));
        glBindAttribLocation(host.program, 0, "pos");
        glLinkProgram(host.program);
        GLint linked = 0;
        glGetProgramiv(host.program, GL_LINK_STATUS, &linked);
        if (!linked) {
            fail("program link failed");
        }
        glUseProgram(host.program);
        glUniform1i(glGetUniformLocation(host.program, "tex0"), 0);
        const float vertices[6] = {-1, -1, 3, -1, -1, 3};
        glGenBuffers(1, &host.vbo);
        glBindBuffer(GL_ARRAY_BUFFER, host.vbo);
        glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices,
                     GL_STATIC_DRAW);
        glEnableVertexAttribArray(0);
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, (void *)0);
        glGenTextures(1, &host.texture);
        glBindTexture(GL_TEXTURE_2D, host.texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 4, 4, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, host.version_a);
        if (glGetError() != GL_NO_ERROR) {
            fail("texture setup failed");
        }

        setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
        setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
        setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
        setenv("EU4_GFX_FRAME_SEAL", "1", 1);
        if (eu4_gfx_frame_trace_configure_from_env() != 1) {
            fail("trace configuration refused");
        }
        memset(&host.frame, 0, sizeof(host.frame));
        memset(&host.call, 0, sizeof(host.call));
        host.call.caller_rva = 0x1000;
        host.call.caller_known = 1;
        host.call.call_id = 1;
        host.call.thread_id = 1;
        run_scenario(&host, scenario);
        glFinish();
        [context flushBuffer];
    }
    return 0;
}
