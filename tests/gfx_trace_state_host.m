/* Original-GL observer state-restoration regression.

   The frame tracer's texture readback binds a texture on the currently active
   unit. This host verifies that the probe used by that path restores the
   active unit's bindings and leaves the other units untouched. */

#import <AppKit/AppKit.h>
#import <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include "eu4_gfx_frame_trace.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) *width = 4;
    if (height != NULL) *height = 4;
    return 0;
}

static GLint binding_of(GLenum target) {
    GLint value = 0;
    glGetIntegerv(target, &value);
    return value;
}

static GLuint make_texture(GLenum target) {
    GLuint name = 0;
    glGenTextures(1, &name);
    glBindTexture(target, name);
    glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    if (target == GL_TEXTURE_2D) {
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 4, 4, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, NULL);
    }
    return name;
}

static GLuint make_texture_internal(GLenum internal, GLenum format,
                                    GLenum type) {
    GLuint name = 0;
    glGenTextures(1, &name);
    glBindTexture(GL_TEXTURE_2D, name);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, (GLint)internal, 4, 4, 0, format, type,
                 NULL);
    return name;
}

static int record_blob_name(const char *record, char *out, size_t size) {
    const char *key = strstr(record, "\"pre_image\"") != NULL
                          ? "\"file\":\"" : "\"color\":{\"file\":\"";
    const char *start = strstr(record, key);
    if (start == NULL) {
        return -1;
    }
    start += strlen(key);
    const char *end = strchr(start, '"');
    if (end == NULL || (size_t)(end - start) >= size) {
        return -1;
    }
    memcpy(out, start, (size_t)(end - start));
    out[end - start] = 0;
    return 0;
}

static int blob_equals(const char *directory, const char *name,
                       const uint8_t *expected, size_t bytes) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/blobs/%s", directory, name);
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return 0;
    }
    uint8_t buffer[64];
    size_t got = bytes <= sizeof(buffer) ? fread(buffer, 1, bytes, file) : 0;
    fclose(file);
    return got == bytes && memcmp(buffer, expected, bytes) == 0;
}

static GLuint fixture_shader(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint compiled = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    return compiled ? shader : 0;
}

static int selected_fixture(const char *directory) {
    GLuint fbo = 0, texture = make_texture(GL_TEXTURE_2D);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 864, 558, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glViewport(0, 0, 864, 558);
    glDisable(GL_DITHER);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    char record[8192];
    if (eu4_gfx_frame_trace_test_observe(
            directory, 0, "selected_pre_draw", "draw", -1, record, sizeof(record)) != 0)
        return 1;
    puts(record);
    GLuint vertex = fixture_shader(GL_VERTEX_SHADER,
        "#version 120\nattribute vec4 in_POSITION; uniform vec4 UNIFORM1[9]; varying vec2 uv; "
        "void main(){ gl_Position=in_POSITION+UNIFORM1[8]; uv=in_POSITION.xy*.5+.5; }\n");
    GLuint fragment = fixture_shader(GL_FRAGMENT_SHADER,
        "#version 120\nvarying vec2 uv; uniform sampler2D tex0; "
        "void main(){ gl_FragColor=texture2D(tex0,uv); }\n");
    if (!vertex || !fragment) return 1;
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glBindAttribLocation(program, 0, "in_POSITION");
    glLinkProgram(program);
    glUseProgram(program);
    const GLshort vertices[] = {-1,-1,0,1, 1,-1,0,1, -1,1,0,1, 1,1,0,1};
    GLuint buffer = 0;
    glGenBuffers(1, &buffer);
    glBindBuffer(GL_ARRAY_BUFFER, buffer);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
    glVertexAttribPointer(0, 4, GL_SHORT, GL_FALSE, 8, 0);
    glEnableVertexAttribArray(0);
    make_texture(GL_TEXTURE_2D);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    uint8_t *pixels = malloc(1728 * 1117 * 4);
    if (!pixels) return 1;
    for (size_t i = 0; i < 1728 * 1117 * 4; i += 4) {
        memcpy(pixels + i, (const uint8_t[]){61, 97, 163, 255}, 4);
    }
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1728, 1117, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    free(pixels);
    fputs("{", stdout);
    if (!eu4_gfx_frame_trace_test_capture_arrays(stdout, GL_TRIANGLE_STRIP, 0, 4)) return 1;
    puts("}");
    if (eu4_gfx_frame_trace_test_observe(
            directory, 0, "post_event", "draw", -1, record, sizeof(record)) != 0)
        return 1;
    puts(record);
    printf("{\"vendor\":\"%s\",\"renderer\":\"%s\",\"version\":\"%s\","
           "\"cgl\":{\"profile\":\"legacy\"}}\n",
           glGetString(GL_VENDOR), glGetString(GL_RENDERER), glGetString(GL_VERSION));
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) {
        fprintf(stderr, "usage: %s <dir>\n", argv[0]);
        return 2;
    }
    NSOpenGLPixelFormatAttribute attributes[] = {
        NSOpenGLPFAAccelerated,
        NSOpenGLPFAColorSize, 24,
        NSOpenGLPFAAlphaSize, 8,
        NSOpenGLPFADepthSize, 24,
        NSOpenGLPFAStencilSize, 8,
        NSOpenGLPFADoubleBuffer,
        0,
    };
    NSOpenGLPixelFormat *format =
        [[NSOpenGLPixelFormat alloc] initWithAttributes:attributes];
    if (format == nil) {
        fprintf(stderr, "pixel format\n");
        return 1;
    }
    [NSApplication sharedApplication];
    NSOpenGLView *view =
        [[NSOpenGLView alloc] initWithFrame:NSMakeRect(0, 0, 8, 8)
                                pixelFormat:format];
    if (view == nil) {
        fprintf(stderr, "view\n");
        return 1;
    }
    NSWindow *window =
        [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 8, 8)
                                    styleMask:NSWindowStyleMaskBorderless
                                      backing:NSBackingStoreBuffered
                                        defer:NO];
    [window setContentView:view];
    [window setFrameOrigin:NSMakePoint(-10000, -10000)];
    [window makeKeyAndOrderFront:nil];
    [view reshape];
    NSOpenGLContext *context = [view openGLContext];
    if (context == nil) {
        fprintf(stderr, "context\n");
        return 1;
    }
    [context setView:view];
    [context update];
    [context makeCurrentContext];
    if (argc == 3 && strcmp(argv[2], "selected") == 0) return selected_fixture(argv[1]);

    GLuint a = make_texture(GL_TEXTURE_2D);
    GLuint b = make_texture(GL_TEXTURE_2D);
    GLuint c = make_texture(GL_TEXTURE_2D);
    GLuint cube_e = make_texture(GL_TEXTURE_CUBE_MAP);
    GLuint cube_f = make_texture(GL_TEXTURE_CUBE_MAP);

    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, a);
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, b);
    glBindTexture(GL_TEXTURE_CUBE_MAP, cube_e);

    /* A pack state that the engine may legitimately leave set at draw time.
       The readback must restore all of it, including a bound pack buffer. */
    glPixelStorei(GL_PACK_ALIGNMENT, 8);
    glPixelStorei(GL_PACK_ROW_LENGTH, 33);
    glPixelStorei(GL_PACK_SKIP_ROWS, 2);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 3);
    GLuint pack_buffer = 0;
    glGenBuffers(1, &pack_buffer);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pack_buffer);

    int ok = 1;
    if (eu4_gfx_frame_trace_texture_probe(GL_TEXTURE_2D, c) != 0) {
        ok = 0;
    }
    GLint active = 0;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    if (active != GL_TEXTURE3) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_2D) != (GLint)b) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_CUBE_MAP) != (GLint)cube_e) {
        ok = 0;
    }
    glActiveTexture(GL_TEXTURE0);
    if (binding_of(GL_TEXTURE_BINDING_2D) != (GLint)a) {
        ok = 0;
    }
    glActiveTexture(GL_TEXTURE3);

    GLint pack_alignment = 0, pack_row = 0, pack_skip_rows = 0;
    GLint pack_skip_pixels = 0, pack_bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_bound);
    if (pack_alignment != 8 || pack_row != 33 || pack_skip_rows != 2 ||
        pack_skip_pixels != 3 || pack_bound != (GLint)pack_buffer) {
        ok = 0;
    }

    /* A cube probe on the active unit restores both target bindings there. */
    if (eu4_gfx_frame_trace_texture_probe(GL_TEXTURE_CUBE_MAP, cube_f) != 0) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_CUBE_MAP) != (GLint)cube_e ||
        binding_of(GL_TEXTURE_BINDING_2D) != (GLint)b) {
        ok = 0;
    }
    /* Draw the intended default back buffer with a known image, then leave a
       different offscreen framebuffer selected as READ/DRAW.  The final
       reference must come from the default back buffer, not attachment zero of
       the selected FBO. */
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDrawBuffer(GL_BACK);
    glReadBuffer(GL_BACK);
    glViewport(0, 0, 4, 4);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClearDepth(0.25);
    glClearStencil(0x5A);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT |
            GL_STENCIL_BUFFER_BIT);
    glFinish();

    /* The default drawable's properties must be queried while framebuffer
       zero is current; later the host deliberately leaves an FBO selected. */
    GLint live_samples = 0, live_sample_buffers = 0, live_depth_bits = 0;
    GLint live_stencil_bits = 0, live_red_bits = 0, live_green_bits = 0;
    GLint live_blue_bits = 0, live_alpha_bits = 0, live_read_buffer = 0;
    glGetIntegerv(GL_SAMPLES, &live_samples);
    glGetIntegerv(GL_SAMPLE_BUFFERS, &live_sample_buffers);
    glGetIntegerv(GL_DEPTH_BITS, &live_depth_bits);
    glGetIntegerv(GL_STENCIL_BITS, &live_stencil_bits);
    glGetIntegerv(GL_RED_BITS, &live_red_bits);
    glGetIntegerv(GL_GREEN_BITS, &live_green_bits);
    glGetIntegerv(GL_BLUE_BITS, &live_blue_bits);
    glGetIntegerv(GL_ALPHA_BITS, &live_alpha_bits);
    glGetIntegerv(GL_READ_BUFFER, &live_read_buffer);

    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "offscreen framebuffer incomplete\n");
        return 1;
    }
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();

    /* Plant one known sentinel error without querying it first.  The observer
       must not drain the engine's queue; glGetError is destructive. */
    while (glGetError() != GL_NO_ERROR) {
    }
    glEnable(0xFFFF);
    int pending = 0x0500;
    if (eu4_gfx_frame_trace_test_reference(argv[1]) != 0) {
        ok = 0;
    }
    /* The selected-event checkpoint uses the same readback contract.  It must
       preserve the same state and must not drain the pending error. */
    char checkpoint_file[256] = "";
    if (eu4_gfx_frame_trace_test_checkpoint(
            argv[1], 142, checkpoint_file, sizeof(checkpoint_file)) != 0) {
        ok = 0;
    }
    /* The observer must leave exactly the sentinel error in the engine's
       queue: draining it or planting an additional error both fail. */
    int after = 0;
    int error_count = 0;
    for (;;) {
        GLenum error = glGetError();
        if (error == GL_NO_ERROR) break;
        if (error_count == 0) after = (int)error;
        error_count++;
    }
    if (error_count != 1 || after != pending) {
        ok = 0;
    }
    GLint draw_after = 0, read_after = 0, read_buffer_after = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_after);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_after);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer_after);
    if (draw_after != (GLint)fbo || read_after != (GLint)fbo ||
        read_buffer_after != GL_COLOR_ATTACHMENT0) {
        ok = 0;
    }
    GLint final_alignment = 0, final_row = 0, final_skip_rows = 0;
    GLint final_skip_pixels = 0, final_bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &final_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &final_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &final_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &final_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &final_bound);
    if (final_alignment != 8 || final_row != 33 || final_skip_rows != 2 ||
        final_skip_pixels != 3 || final_bound != (GLint)pack_buffer) {
        ok = 0;
    }

    char reference_path[1024];
    snprintf(reference_path, sizeof(reference_path),
             "%s/frame-reference.bgra", argv[1]);
    FILE *reference = fopen(reference_path, "rb");
    int reference_ok = 0;
    if (reference != NULL) {
        uint8_t pixels[4 * 4 * 4];
        size_t got = fread(pixels, 1, sizeof(pixels), reference);
        int extra = fgetc(reference);
        fclose(reference);
        reference_ok = got == sizeof(pixels) && extra == EOF;
        for (size_t i = 0; reference_ok && i < 16; i++) {
            const uint8_t expected[4] = {191, 128, 64, 255};
            if (memcmp(pixels + i * 4, expected, 4) != 0) {
                reference_ok = 0;
            }
        }
    }
    if (!reference_ok) {
        ok = 0;
    }

    char checkpoint_path[1024];
    snprintf(checkpoint_path, sizeof(checkpoint_path), "%s/blobs/%s", argv[1],
             checkpoint_file);
    FILE *checkpoint = checkpoint_file[0] != 0 ? fopen(checkpoint_path, "rb")
                                               : NULL;
    int checkpoint_ok = 0;
    if (checkpoint != NULL) {
        uint8_t pixels[4 * 4 * 4];
        size_t got = fread(pixels, 1, sizeof(pixels), checkpoint);
        int extra = fgetc(checkpoint);
        fclose(checkpoint);
        checkpoint_ok = got == sizeof(pixels) && extra == EOF;
        for (size_t i = 0; checkpoint_ok && i < 16; i++) {
            const uint8_t expected[4] = {191, 128, 64, 255};
            if (memcmp(pixels + i * 4, expected, 4) != 0) {
                checkpoint_ok = 0;
            }
        }
    }
    if (!checkpoint_ok) {
        ok = 0;
    }

    /* The checkpoint now carries the default drawable's queried properties and
       the exact pre-draw depth/stencil contents.  Verify both against the live
       driver, never against a hard-coded offscreen expectation. */
    char records[4096] = "";
    char expected[512] = "";
    char depth_blob[256] = "";
    char stencil_blob[256] = "";
    int records_ok = 0;
    if (eu4_gfx_frame_trace_test_checkpoint_records(
            records, sizeof(records), depth_blob, sizeof(depth_blob),
            stencil_blob, sizeof(stencil_blob)) == 0) {
        snprintf(expected, sizeof(expected),
                 "\"drawable\":{\"samples\":%d,\"sample_buffers\":%d,"
                 "\"depth_bits\":%d,\"stencil_bits\":%d,"
                 "\"red_bits\":%d,\"green_bits\":%d,\"blue_bits\":%d,"
                 "\"alpha_bits\":%d",
                 live_samples, live_sample_buffers, live_depth_bits,
                 live_stencil_bits, live_red_bits, live_green_bits,
                 live_blue_bits, live_alpha_bits);
        records_ok = strstr(records, "\"schema\":\"gfx-default-framebuffer-checkpoint/v2\"") != NULL &&
                     strstr(records, expected) != NULL &&
                     strstr(records, "\"depth\":{\"file\":\"") != NULL &&
                     strstr(records, "\"format\":\"f32\"") != NULL &&
                     strstr(records, "\"stencil\":{\"file\":\"") != NULL &&
                     strstr(records, "\"format\":\"u8\"") != NULL &&
                     strstr(records, "\"framebuffer_srgb\":") != NULL &&
                     strstr(records, "\"dither\":") != NULL &&
                     strstr(records, "\"read_buffer\":") != NULL &&
                     depth_blob[0] != 0 && stencil_blob[0] != 0;
    }
    if (!records_ok) {
        ok = 0;
    }
    char depth_path[1024];
    snprintf(depth_path, sizeof(depth_path), "%s/blobs/%s", argv[1],
             depth_blob);
    FILE *depth = depth_blob[0] != 0 ? fopen(depth_path, "rb") : NULL;
    int depth_ok = 0;
    if (depth != NULL) {
        float values[4 * 4];
        size_t got = fread(values, sizeof(float), 16, depth);
        int extra = fgetc(depth);
        fclose(depth);
        depth_ok = got == 16 && extra == EOF;
        for (int i = 0; depth_ok && i < 16; i++) {
            if (values[i] < 0.2499f || values[i] > 0.2501f) {
                depth_ok = 0;
            }
        }
    }
    if (!depth_ok) {
        ok = 0;
    }
    char stencil_path[1024];
    snprintf(stencil_path, sizeof(stencil_path), "%s/blobs/%s", argv[1],
             stencil_blob);
    FILE *stencil_file = stencil_blob[0] != 0 ? fopen(stencil_path, "rb")
                                              : NULL;
    int stencil_ok = 0;
    if (stencil_file != NULL) {
        uint8_t values[4 * 4];
        size_t got = fread(values, 1, 16, stencil_file);
        int extra = fgetc(stencil_file);
        fclose(stencil_file);
        stencil_ok = got == 16 && extra == EOF;
        for (int i = 0; stencil_ok && i < 16; i++) {
            if (values[i] != 0x5A) {
                stencil_ok = 0;
            }
        }
    }
    if (!stencil_ok) {
        ok = 0;
    }

    /* The production selected-event path gates depth/stencil readback on the
       live test state.  Exercise both/one/neither enabled and assert the
       unused buffer is recorded as absent rather than aborting the capture. */
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDrawBuffer(GL_BACK);
    glReadBuffer(GL_BACK);
    glViewport(0, 0, 4, 4);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClearDepth(0.25);
    glClearStencil(0x5A);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT |
            GL_STENCIL_BUFFER_BIT);
    int production_none = 0, production_depth = 0, production_both = 0;
    for (int mode = 0; mode < 3; mode++) {
        int depth_enabled = mode >= 1;
        int stencil_enabled = mode >= 2;
        if (depth_enabled) glEnable(GL_DEPTH_TEST);
        else glDisable(GL_DEPTH_TEST);
        if (stencil_enabled) glEnable(GL_STENCIL_TEST);
        else glDisable(GL_STENCIL_TEST);
        char record[4096] = "";
        char dfile[256] = "";
        char sfile[256] = "";
        char colour_file[256] = "";
        if (eu4_gfx_frame_trace_test_checkpoint_production(
                argv[1], 200 + mode, colour_file, sizeof(colour_file)) != 0 ||
            eu4_gfx_frame_trace_test_checkpoint_records(
                record, sizeof(record), dfile, sizeof(dfile), sfile,
                sizeof(sfile)) != 0 ||
            colour_file[0] == 0) {
            continue;
        }
        int depth_absent = strstr(
            record, "\"depth\":{\"status\":\"absent\"") != NULL;
        int stencil_absent = strstr(
            record, "\"stencil\":{\"status\":\"absent\"") != NULL;
        int depth_present = dfile[0] != 0 && !depth_absent;
        int stencil_present = sfile[0] != 0 && !stencil_absent;
        if (mode == 0 && depth_absent && stencil_absent) {
            production_none = 1;
        } else if (mode == 1 && depth_present && stencil_absent) {
            production_depth = 1;
        } else if (mode == 2 && depth_present && stencil_present) {
            production_both = 1;
        }
    }
    if (!production_none || !production_depth || !production_both) {
        ok = 0;
    }
    /* The shared observation primitive (default drawable, offscreen texture,
       RGB8, SRGB8_ALPHA8, R32F) must preserve state and never consume a pending
       engine error. */
    int observation = 1;
    char record[8192] = "";
    char blob[256] = "";
    while (glGetError() != GL_NO_ERROR) {
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDrawBuffer(GL_BACK);
    glReadBuffer(GL_BACK);
    glViewport(0, 0, 4, 4);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    glPixelStorei(GL_PACK_ALIGNMENT, 8);
    glPixelStorei(GL_PACK_ROW_LENGTH, 33);
    glPixelStorei(GL_PACK_SKIP_ROWS, 2);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 3);
    glPixelStorei(GL_PACK_SWAP_BYTES, GL_TRUE);
    glPixelStorei(GL_PACK_LSB_FIRST, GL_TRUE);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pack_buffer);
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, b);
    glEnable(0xFFFF);
    int sentinel = 0x0500;
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "pre_event", "initial", -1, record,
            sizeof(record)) != 0) {
        observation = 0;
    }
    if (strstr(record, "\"pixel_format\":\"BGRA8\"") == NULL ||
        strstr(record, "\"alpha_applicable\":true") == NULL ||
        strstr(record, "\"internal_format\":0") == NULL ||
        strstr(record, "\"alpha_bits\":8") == NULL) {
        observation = 0;
    }
    {
        const uint8_t expected[4] = {191, 128, 64, 255};
        if (record_blob_name(record, blob, sizeof(blob)) != 0 ||
            !blob_equals(argv[1], blob, expected, sizeof(expected))) {
            observation = 0;
        }
    }
    int obs_error_count = 0;
    int error_after = 0;
    for (;;) {
        GLenum error = glGetError();
        if (error == GL_NO_ERROR) break;
        if (obs_error_count == 0) error_after = (int)error;
        obs_error_count++;
    }
    if (obs_error_count != 1 || error_after != sentinel) {
        observation = 0;
    }
    GLint obs_draw_after = 0, obs_read_after = 0, obs_read_buffer_after = 0;
    GLboolean obs_pack_swap = GL_FALSE, obs_pack_lsb = GL_FALSE;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &obs_draw_after);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &obs_read_after);
    glGetIntegerv(GL_READ_BUFFER, &obs_read_buffer_after);
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_bound);
    glGetBooleanv(GL_PACK_SWAP_BYTES, &obs_pack_swap);
    glGetBooleanv(GL_PACK_LSB_FIRST, &obs_pack_lsb);
    GLint texture_binding = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture_binding);
    if (obs_draw_after != 0 || obs_read_after != 0 || obs_read_buffer_after != GL_BACK ||
        pack_alignment != 8 || pack_row != 33 || pack_skip_rows != 2 ||
        pack_skip_pixels != 3 || pack_bound != (GLint)pack_buffer ||
        obs_pack_swap != GL_TRUE || obs_pack_lsb != GL_TRUE ||
        texture_binding != (GLint)b) {
        observation = 0;
    }

    /* Offscreen RGBA8 texture, post-event copy/resolve source. */
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    while (glGetError() != GL_NO_ERROR) {
    }
    glEnable(0xFFFF);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", (int)c, record,
            sizeof(record)) != 0) {
        observation = 0;
    }
    if (strstr(record, "\"pixel_format\":\"RGBA8\"") == NULL ||
        strstr(record, "\"alpha_applicable\":true") == NULL ||
        strstr(record, "\"internal_format\":32856") == NULL) {
        observation = 0;
    }
    {
        const uint8_t expected[4] = {255, 0, 0, 255};
        if (record_blob_name(record, blob, sizeof(blob)) != 0 ||
            !blob_equals(argv[1], blob, expected, sizeof(expected))) {
            observation = 0;
        }
    }
    obs_error_count = 0;
    error_after = 0;
    for (;;) {
        GLenum error = glGetError();
        if (error == GL_NO_ERROR) break;
        if (obs_error_count == 0) error_after = (int)error;
        obs_error_count++;
    }
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &obs_draw_after);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &obs_read_after);
    glGetIntegerv(GL_READ_BUFFER, &obs_read_buffer_after);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture_binding);
    if (obs_error_count != 1 || error_after != sentinel ||
        obs_draw_after != (GLint)fbo || obs_read_after != (GLint)fbo ||
        obs_read_buffer_after != GL_COLOR_ATTACHMENT0 ||
        texture_binding != (GLint)b) {
        observation = 0;
    }

    /* Caller-selected readback uses a complete color-only FBO, a direct
       pre-draw BGRA8 seed and a separate original RGBA8 post observation. */
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "selected_pre_draw", "draw", -1, record, sizeof(record)) != 0 ||
        strstr(record, "\"pre_image\"") == NULL ||
        strstr(record, "\"internal_format\":32856") == NULL ||
        strstr(record, "\"pixel_format\":\"BGRA8\"") == NULL) {
        observation = 0;
        fprintf(stderr, "selected pre-draw readback: %s\n", record);
    }
    {
        const uint8_t expected[4] = {0, 0, 255, 255};
        if (record_blob_name(record, blob, sizeof(blob)) != 0 ||
            !blob_equals(argv[1], blob, expected, sizeof(expected))) {
            observation = 0;
        }
    }
    glClearColor(0.0f, 0.0f, 1.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "draw", -1, record, sizeof(record)) != 0 ||
        strstr(record, "\"pixel_format\":\"RGBA8\"") == NULL ||
        strstr(record, "\"depth_present\":false") == NULL ||
        strstr(record, "\"stencil_present\":false") == NULL) {
        observation = 0;
        fprintf(stderr, "selected post-draw readback: %s\n", record);
    }
    {
        const uint8_t expected[4] = {0, 0, 255, 255};
        if (record_blob_name(record, blob, sizeof(blob)) != 0 ||
            !blob_equals(argv[1], blob, expected, sizeof(expected))) {
            observation = 0;
        }
    }
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_bound);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture_binding);
    GLenum selected_error = glGetError();
    if (pack_bound != (GLint)pack_buffer || texture_binding != (GLint)b ||
        selected_error != GL_NO_ERROR) {
        observation = 0;
        fprintf(stderr, "selected readback state PBO=%d texture=%d GL error=0x%x\n",
                pack_bound, texture_binding, selected_error);
    }

    /* RGB8 has no stored alpha; the readback is BGRA8 and alpha is out of
       scope. */
    GLuint rgb8 = make_texture_internal(GL_RGB8, GL_RGB, GL_UNSIGNED_BYTE);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, rgb8, 0);
    glClearColor(0.0f, 1.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", (int)rgb8, record,
            sizeof(record)) != 0 ||
        strstr(record, "\"pixel_format\":\"BGRA8\"") == NULL ||
        strstr(record, "\"alpha_applicable\":false") == NULL ||
        strstr(record, "\"internal_format\":32849") == NULL) {
        observation = 0;
    }

    /* SRGB8_ALPHA8 is a BGRA8 byte layout with a stored alpha channel. */
    GLuint srgb = make_texture_internal(0x8C43, GL_RGBA, GL_UNSIGNED_BYTE);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, srgb, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", (int)srgb, record,
            sizeof(record)) != 0 ||
        strstr(record, "\"pixel_format\":\"BGRA8\"") == NULL ||
        strstr(record, "\"alpha_applicable\":true") == NULL ||
        strstr(record, "\"internal_format\":35907") == NULL) {
        observation = 0;
    }

    /* R32F is one exact 32-bit float per pixel with no alpha. */
    GLuint r32f = make_texture_internal(0x822E, GL_RED, GL_FLOAT);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, r32f, 0);
    glClearColor(0.5f, 0.0f, 0.0f, 0.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", (int)r32f, record,
            sizeof(record)) != 0 ||
        strstr(record, "\"pixel_format\":\"R32F\"") == NULL ||
        strstr(record, "\"alpha_applicable\":false") == NULL ||
        strstr(record, "\"internal_format\":33326") == NULL) {
        observation = 0;
    }

    /* Unsupported attachment/readback configurations must fail closed before a
       zero-initialized readback can be published. */
    int negative = 1;
    char negative_record[4096] = "";
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    glDrawBuffer(GL_COLOR_ATTACHMENT1);
    glReadBuffer(GL_COLOR_ATTACHMENT1);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", -1, negative_record,
            sizeof(negative_record)) == 0) {
        negative = 0;
    }
    glDrawBuffer(GL_NONE);
    glReadBuffer(GL_NONE);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", -1, negative_record,
            sizeof(negative_record)) == 0) {
        negative = 0;
    }
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, 0, 0);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", -1, negative_record,
            sizeof(negative_record)) == 0) {
        negative = 0;
    }
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    GLuint cube = make_texture(GL_TEXTURE_CUBE_MAP);
    glBindTexture(GL_TEXTURE_CUBE_MAP, cube);
    glTexImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X, 0, GL_RGBA8, 4, 4, 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glTexParameteri(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_CUBE_MAP_POSITIVE_X, cube, 0);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", -1, negative_record,
            sizeof(negative_record)) == 0) {
        negative = 0;
    }
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    GLuint renderbuffer = 0;
    glGenRenderbuffers(1, &renderbuffer);
    glBindRenderbuffer(GL_RENDERBUFFER, renderbuffer);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, 4, 4);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                              GL_RENDERBUFFER, renderbuffer);
    if (eu4_gfx_frame_trace_test_observe(
            argv[1], 0, "post_event", "resolve", -1, negative_record,
            sizeof(negative_record)) == 0) {
        negative = 0;
    }
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    /* The manifest's default-drawable identity must be queried with
       framebuffer zero selected even when an incomplete/timeout path leaves a
       user FBO bound.  Querying GL_BACK_LEFT against the FBO would report
       "absent" with a status of "ok". */
    int manifest_ok = 1;
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    while (glGetError() != GL_NO_ERROR) {
    }
    if (eu4_gfx_frame_trace_test_manifest(argv[1]) != 0) {
        manifest_ok = 0;
    }
    GLint manifest_draw = 0, manifest_read = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &manifest_draw);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &manifest_read);
    if (manifest_draw != (GLint)fbo || manifest_read != (GLint)fbo) {
        manifest_ok = 0;
    }
    char manifest_path[1024];
    snprintf(manifest_path, sizeof(manifest_path), "%s/frame-trace.json",
             argv[1]);
    FILE *manifest = fopen(manifest_path, "rb");
    if (manifest == NULL) {
        manifest_ok = 0;
    } else {
        char manifest_text[65536];
        size_t got = fread(manifest_text, 1, sizeof(manifest_text) - 1,
                           manifest);
        manifest_text[got] = 0;
        fclose(manifest);
        if (strstr(manifest_text, "\"default_drawable\":{\"status\":\"ok\"")
                == NULL ||
            strstr(manifest_text,
                   "\"color_back_left\":{\"object_type\":\"framebuffer_default\"")
                == NULL) {
            manifest_ok = 0;
        }
    }
    if (!manifest_ok) {
        ok = 0;
    }

    printf("trace_state=%d pending=0x%x after=0x%x reference=%d "
           "checkpoint=%d depth=%d stencil=%d records=%d production=%d%d%d "
           "observation=%d negative=%d manifest=%d\n",
           ok, pending, after, reference_ok, checkpoint_ok, depth_ok,
           stencil_ok, records_ok, production_none, production_depth,
           production_both, observation, negative, manifest_ok);
    return ok && observation && negative ? 0 : 1;
}
