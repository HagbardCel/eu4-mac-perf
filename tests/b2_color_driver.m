#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *sym(const char *name) {
    void *found = dlsym(RTLD_DEFAULT, name);
    if (found == NULL) {
        fprintf(stderr, "missing %s\n", name);
        exit(1);
    }
    return found;
}

static char *read_file(const char *path) {
    FILE *in = fopen(path, "rb");
    if (in == NULL) {
        fprintf(stderr, "cannot read %s\n", path);
        exit(1);
    }
    fseek(in, 0, SEEK_END);
    long len = ftell(in);
    fseek(in, 0, SEEK_SET);
    char *data = malloc((size_t)len + 1);
    if (data == NULL || fread(data, 1, (size_t)len, in) != (size_t)len) {
        fprintf(stderr, "cannot read %s\n", path);
        exit(1);
    }
    data[len] = 0;
    fclose(in);
    return data;
}

static int fixture_present(void) {
    const char *dir = getenv("EU4_B2_COLOR_DIR");
    char path[1200];
    snprintf(path, sizeof path, "%s/fixture/fixture.json", dir);
    FILE *in = fopen(path, "r");
    if (in == NULL) {
        return 0;
    }
    fclose(in);
    return 1;
}

static void expect_fixture(int want) {
    if (fixture_present() != want) {
        fprintf(stderr, "fixture %s\n", want ? "missing" : "unexpected");
        exit(1);
    }
}

static void make_context(void) {
    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8, 0
    };
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    CGLContextObj context = NULL;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || format == NULL ||
        CGLCreateContext(format, NULL, &context) != kCGLNoError || CGLSetCurrentContext(context) != kCGLNoError) {
        fprintf(stderr, "no legacy gl context\n");
        exit(1);
    }
    CGLDestroyPixelFormat(format);
    GLuint color = 0;
    GLuint fbo = 0;
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 32, 32, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "fbo incomplete\n");
        exit(1);
    }
    while (glGetError() != GL_NO_ERROR) {
    }
}

static GLuint compile_one(GLenum type, const char *text) {
    typedef void (*SourceFn)(GLuint, GLsizei, const GLchar *const *, const GLint *);
    SourceFn source = sym("glShaderSource");
    GLuint shader = glCreateShader(type);
    source(shader, 1, &text, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024];
        glGetShaderInfoLog(shader, (GLsizei)sizeof log, NULL, log);
        fprintf(stderr, "%s\n", log);
        exit(1);
    }
    return shader;
}

static GLuint build_program(const char *vert, const char *frag, GLuint *out_frag) {
    typedef void (*LinkFn)(GLuint);
    GLuint vs = compile_one(GL_VERTEX_SHADER, vert);
    GLuint fs = compile_one(GL_FRAGMENT_SHADER, frag);
    GLuint program = glCreateProgram();
    glAttachShader(program, vs);
    glAttachShader(program, fs);
    glBindAttribLocation(program, 3, "in_POSITION");
    glBindAttribLocation(program, 5, "in_PDX_COLOR");
    LinkFn link = sym("glLinkProgram");
    link(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        char log[1024];
        glGetProgramInfoLog(program, (GLsizei)sizeof log, NULL, log);
        fprintf(stderr, "%s\n", log);
        exit(1);
    }
    glUseProgram(program);
    if (out_frag) {
        *out_frag = fs;
    }
    return program;
}

static void ortho(float m[16], float clip_z) {
    memset(m, 0, sizeof(float) * 16);
    m[0] = 2.f / 32.f;
    m[5] = 2.f / 32.f;
    m[10] = 1.f;
    m[12] = -1.f;
    m[13] = -1.f;
    m[14] = clip_z;
    m[15] = 1.f;
}

static void upload(GLenum index_type, const void *index_bytes, int index_bytes_len) {
    (void)index_type;
    uint8_t verts[36];
    float xy[3][2] = {{6.f, 6.f}, {6.f, 26.5f}, {26.5f, 6.f}};
    uint8_t fill[4] = {128, 64, 192, 255};
    for (int i = 0; i < 3; i++) {
        memcpy(verts + i * 12, xy[i], 8);
        memcpy(verts + i * 12 + 8, fill, 4);
    }
    GLuint vbo = 0;
    GLuint ibo = 0;
    glGenBuffers(1, &vbo);
    glGenBuffers(1, &ibo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, 36, verts, GL_STATIC_DRAW);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, index_bytes_len, index_bytes, GL_STATIC_DRAW);
    glVertexAttribPointer(3, 2, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glEnableVertexAttribArray(3);
    glVertexAttribPointer(5, 4, GL_UNSIGNED_BYTE, GL_TRUE, 12, (void *)8);
    glEnableVertexAttribArray(5);
}

static void fixed_state(int blend, int dither) {
    glViewport(0, 0, 32, 32);
    glDisable(GL_DEPTH_TEST);
    if (blend) {
        glEnable(GL_BLEND);
    } else {
        glDisable(GL_BLEND);
    }
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_ALPHA_TEST);
    glDisable(GL_COLOR_LOGIC_OP);
    glDisable(GL_POLYGON_SMOOTH);
    for (int i = 0; i < 6; i++) {
        glDisable(GL_CLIP_PLANE0 + i);
    }
    if (dither) {
        glEnable(GL_DITHER);
    } else {
        glDisable(GL_DITHER);
    }
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    glFrontFace(GL_CW);
    glPolygonMode(GL_FRONT_AND_BACK, GL_FILL);
    glColorMask(1, 1, 1, 1);
}

static void issue(const char *symbol, GLenum type, GLsizei count, const void *offset) {
    typedef void (*DrawFn)(GLenum, GLsizei, GLenum, const void *);
    DrawFn draw = sym(symbol);
    draw(GL_TRIANGLES, count, type, offset);
}

static void set_matrix(GLuint program, float clip_z) {
    float matrix[16];
    ortho(matrix, clip_z);
    glUniform4fv(glGetUniformLocation(program, "UNIFORM0"), 4, matrix);
}

static void clear_target(void) {
    glClearColor(16.f / 255.f, 32.f / 255.f, 48.f / 255.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
}

static void expect_drawn(void) {
    unsigned char pixel[4] = {0, 0, 0, 0};
    glReadPixels(13, 13, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    if (pixel[0] == 16 && pixel[1] == 32 && pixel[2] == 48 && pixel[3] == 255) {
        fprintf(stderr, "framebuffer still clear\n");
        exit(1);
    }
}

static int indexed_count(void) {
    int (*count)(void) = sym("eu4_b2_color_indexed_count");
    return count();
}

static void check_forward(unsigned mode, int count, unsigned type, unsigned long long offset, int total) {
    unsigned got_mode = 0;
    int got_count = 0;
    unsigned got_type = 0;
    unsigned long long got_offset = 0;
    int (*forward_count)(void) = sym("eu4_b2_color_forward_count");
    void (*last_forward)(unsigned *, int *, unsigned *, unsigned long long *) = sym("eu4_b2_color_last_forward");
    last_forward(&got_mode, &got_count, &got_type, &got_offset);
    if (forward_count() != total || got_mode != mode || got_count != count || got_type != type || got_offset != offset) {
        fprintf(stderr, "forward got count=%d mode=%u n=%d type=%u offset=%llu\n", forward_count(), got_mode, got_count,
                got_type, got_offset);
        exit(1);
    }
}

static int cached_state(GLuint program) {
    int (*cached)(unsigned) = sym("eu4_b2_color_cached_state");
    return cached(program);
}

static char *used_extra(const char *frag) {
    const char *needle = "gl_FragColor = glsl_main(glsl_vertex);";
    const char *found = strstr(frag, needle);
    if (found == NULL) {
        fprintf(stderr, "fragment pattern missing\n");
        exit(1);
    }
    const char *eol = strchr(frag, '\n');
    if (eol == NULL) {
        fprintf(stderr, "fragment has no version line\n");
        exit(1);
    }
    const char *prefix = "\nuniform vec4 EXTRA;\n";
    const char *replace = "gl_FragColor = glsl_main(glsl_vertex) + EXTRA;";
    size_t version = (size_t)(eol - frag) + 1;
    size_t head = (size_t)(found - frag);
    size_t tail = strlen(found + strlen(needle));
    char *out = malloc(version + strlen(prefix) + (head - version) + strlen(replace) + tail + 1);
    memcpy(out, frag, version);
    memcpy(out + version, prefix, strlen(prefix));
    memcpy(out + version + strlen(prefix), frag + version, head - version);
    size_t at = version + strlen(prefix) + (head - version);
    memcpy(out + at, replace, strlen(replace));
    memcpy(out + at + strlen(replace), found + strlen(needle), tail + 1);
    return out;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: b2_color_driver <scenario>\n");
        return 2;
    }
    if (strcmp(argv[1], "match-vertex") == 0 || strcmp(argv[1], "match-fragment") == 0) {
        char *reference = read_file(argv[2]);
        char *source = read_file(argv[3]);
        int (*matches)(const char *, const char *) =
            sym(argv[1][6] == 'v' ? "eu4_b2_color_vertex_matches" : "eu4_b2_color_fragment_matches");
        return matches(source, reference) ? 0 : 1;
    }
    if (strcmp(argv[1], "range") == 0) {
        int (*ok)(unsigned long long, unsigned long long, unsigned long long, unsigned long long) =
            sym("eu4_b2_color_index_range_ok");
        unsigned long long offset = strtoull(argv[2], NULL, 10);
        unsigned long long count = strtoull(argv[3], NULL, 10);
        unsigned long long size = strtoull(argv[4], NULL, 10);
        unsigned long long bytes = strtoull(argv[5], NULL, 10);
        int want = strcmp(argv[6], "ok") == 0;
        return ok(offset, count, size, bytes) == want ? 0 : 1;
    }

    make_context();
    const char *vert_path = getenv("EU4_B2_COLOR_VERT");
    const char *frag_path = getenv("EU4_B2_COLOR_FRAG");
    char *vert = read_file(vert_path);
    char *frag = read_file(frag_path);
    uint16_t shorts[3] = {0, 1, 2};
    uint32_t ints[3] = {0, 1, 2};
    if (strcmp(argv[1], "symbols") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, 1);
        /* Direct and dlsym("glDrawElements") forwarding is required. ARB forwarding is
         * checked only when that entry point is exported. A missing optional symbol
         * preserves NULL and does not fail preflight. */
        typedef void (*DrawFn)(GLenum, GLsizei, GLenum, const void *);
        DrawFn arb = dlsym(RTLD_DEFAULT, "glDrawElementsARB");
        if (arb != NULL) {
            arb(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, (void *)0);
            check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, 2);
        }
        if (dlsym(RTLD_DEFAULT, "eu4_b2_no_such_gl_symbol") != NULL) {
            fprintf(stderr, "nonexistent symbol was fabricated\n");
            return 1;
        }
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "accept") == 0 || strcmp(argv[1], "dither-on") == 0 || strcmp(argv[1], "uint32") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        int wide = strcmp(argv[1], "uint32") == 0;
        upload(wide ? GL_UNSIGNED_INT : GL_UNSIGNED_SHORT, wide ? (void *)ints : (void *)shorts,
               wide ? (int)sizeof ints : (int)sizeof shorts);
        fixed_state(0, strcmp(argv[1], "dither-on") == 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", wide ? GL_UNSIGNED_INT : GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(1);
        check_forward(GL_TRIANGLES, 3, wide ? GL_UNSIGNED_INT : GL_UNSIGNED_SHORT, 0, 1);
        return 0;
    }
    if (strcmp(argv[1], "reject-then-accept") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        fixed_state(0, 0);
        set_matrix(program, 0.25f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(1);
        char path[1200];
        snprintf(path, sizeof path, "%s/fixture/uniform0.bin", getenv("EU4_B2_COLOR_DIR"));
        FILE *in = fopen(path, "rb");
        float matrix[16];
        if (in == NULL || fread(matrix, sizeof(float), 16, in) != 16 || matrix[14] != 0.25f) {
            fprintf(stderr, "accepted matrix is not the second draw\n");
            return 1;
        }
        fclose(in);
        return 0;
    }
    if (strcmp(argv[1], "nonfinite") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(0, 0);
        float matrix[16];
        ortho(matrix, 0.f);
        matrix[0] = nanf("");
        glUniform4fv(glGetUniformLocation(program, "UNIFORM0"), 4, matrix);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "oob") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        uint16_t bad[3] = {0, 1, 99};
        upload(GL_UNSIGNED_SHORT, bad, (int)sizeof bad);
        fixed_state(0, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "mapped") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(0, 0);
        set_matrix(program, 0.f);
        if (glMapBuffer(GL_ARRAY_BUFFER, GL_READ_ONLY) == NULL) {
            fprintf(stderr, "map failed\n");
            return 1;
        }
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        glUnmapBuffer(GL_ARRAY_BUFFER);
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "relink") == 0) {
        GLuint frag_shader = 0;
        GLuint program = build_program(vert, frag, &frag_shader);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        if (cached_state(program) != 1) {
            fprintf(stderr, "identity was not cached\n");
            return 1;
        }
        char *edited = used_extra(frag);
        typedef void (*SourceFn)(GLuint, GLsizei, const GLchar *const *, const GLint *);
        typedef void (*LinkFn)(GLuint);
        SourceFn source = sym("glShaderSource");
        source(frag_shader, 1, (const GLchar *const *)&edited, NULL);
        glCompileShader(frag_shader);
        LinkFn link = sym("glLinkProgram");
        link(program);
        if (cached_state(program) != -1) {
            fprintf(stderr, "relink left the cached identity\n");
            return 1;
        }
        fixed_state(0, 0);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "shader-edit") == 0) {
        GLuint frag_shader = 0;
        GLuint program = build_program(vert, frag, &frag_shader);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        const char *junk = "void main(){ gl_FragColor = vec4(1.0); }\n";
        typedef void (*SourceFn)(GLuint, GLsizei, const GLchar *const *, const GLint *);
        SourceFn source = sym("glShaderSource");
        source(frag_shader, 1, &junk, NULL);
        if (cached_state(program) != -2) {
            fprintf(stderr, "source edit did not stick until link\n");
            return 1;
        }
        fixed_state(0, 0);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "delete") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        typedef void (*DeleteFn)(GLuint);
        DeleteFn delete_program = sym("glDeleteProgram");
        delete_program(program);
        if (cached_state(program) != -1) {
            fprintf(stderr, "delete left the cached identity\n");
            return 1;
        }
        typedef void (*DeleteArb)(void *);
        DeleteArb delete_arb = sym("glDeleteObjectARB");
        delete_arb((void *)(uintptr_t)program);
        char *edited = used_extra(frag);
        GLuint again = build_program(vert, edited, NULL);
        fixed_state(0, 0);
        set_matrix(again, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        expect_fixture(0);
        if (again == program && cached_state(again) == 1) {
            fprintf(stderr, "reused program id kept the old identity\n");
            return 1;
        }
        return 0;
    }
    if (strcmp(argv[1], "arb-link") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        typedef void (*LinkArb)(void *);
        LinkArb link = sym("glLinkProgramARB");
        link((void *)(uintptr_t)program);
        if (cached_state(program) != -1) {
            fprintf(stderr, "ARB link left the cached identity\n");
            return 1;
        }
        expect_fixture(0);
        return 0;
    }
    if (strcmp(argv[1], "bindings") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(0, 0);
        set_matrix(program, 0.f);
        glEnable(0);
        GLint array_before = 0;
        GLint element_before = 0;
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &array_before);
        glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &element_before);
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        GLint array_after = 0;
        GLint element_after = 0;
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &array_after);
        glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &element_after);
        GLenum error = glGetError();
        if (array_after != array_before || element_after != element_before || error != GL_INVALID_ENUM) {
            fprintf(stderr, "bindings %d/%d -> %d/%d error 0x%x\n", array_before, element_before, array_after,
                    element_after, error);
            return 1;
        }
        expect_fixture(1);
        return 0;
    }
    if (strcmp(argv[1], "reject") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: b2_color_driver reject <kind>\n");
            return 2;
        }
        char *edited = NULL;
        const char *frag_text = frag;
        if (strcmp(argv[2], "extra-uniform") == 0) {
            edited = used_extra(frag);
            frag_text = edited;
        }
        GLuint program = build_program(vert, frag_text, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        fixed_state(0, 0);
        set_matrix(program, 0.f);
        if (strcmp(argv[2], "disabled-color") == 0) {
            glDisableVertexAttribArray(5);
        } else if (strcmp(argv[2], "stride") == 0) {
            glVertexAttribPointer(3, 2, GL_FLOAT, GL_FALSE, 16, (void *)0);
            glVertexAttribPointer(5, 4, GL_UNSIGNED_BYTE, GL_TRUE, 16, (void *)8);
        } else if (strcmp(argv[2], "depth") == 0) {
            glEnable(GL_DEPTH_TEST);
        } else if (strcmp(argv[2], "scissor") == 0) {
            glEnable(GL_SCISSOR_TEST);
        } else if (strcmp(argv[2], "cull-off") == 0) {
            glDisable(GL_CULL_FACE);
        } else if (strcmp(argv[2], "extra-uniform") != 0) {
            fprintf(stderr, "unknown reject %s\n", argv[2]);
            return 2;
        }
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, 1);
        expect_fixture(0);
        free(edited);
        return 0;
    }
    if (strcmp(argv[1], "dispatch") == 0) {
        GLuint program = build_program(vert, frag, NULL);
        upload(GL_UNSIGNED_SHORT, shorts, (int)sizeof shorts);
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 12, (void *)0);
        glEnableVertexAttribArray(0);
        fixed_state(1, 0);
        set_matrix(program, 0.f);
        clear_target();
        int total = 0;
        glDrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, ++total);
        expect_drawn();
        issue("glDrawElements", GL_UNSIGNED_SHORT, 3, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, ++total);
        /* Direct and dlsym("glDrawElements") forwarding is required. ARB forwarding is
         * checked only when that entry point is exported. A missing optional symbol
         * preserves NULL and does not fail preflight. */
        typedef void (*DrawFn)(GLenum, GLsizei, GLenum, const void *);
        DrawFn arb = dlsym(RTLD_DEFAULT, "glDrawElementsARB");
        if (arb != NULL) {
            arb(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, (void *)0);
            check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, ++total);
        }
        expect_fixture(0);
        fixed_state(0, 0);
        set_matrix(program, 0.f);
        glDrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, ++total);
        expect_fixture(1);
        int indexed = indexed_count();
        glDrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0);
        check_forward(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, 0, ++total);
        if (indexed_count() != indexed) {
            fprintf(stderr, "indexed %d then %d\n", indexed, indexed_count());
            return 1;
        }
        expect_fixture(1);
        return 0;
    }
    fprintf(stderr, "unknown scenario %s\n", argv[1]);
    return 2;
}
