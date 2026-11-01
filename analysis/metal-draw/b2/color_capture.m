#define GL_SILENCE_DEPRECATION
#include <CommonCrypto/CommonDigest.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>
#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef GL_PRIMITIVE_RESTART
#define GL_PRIMITIVE_RESTART 0x8F9D
#endif

/* Dither is captured, not required off: the historical draw never recorded it, and GL defaults it on.
   Inspection is after the original draw and must not consume a sticky GL error. */

enum {
    kMaxShader = 128 * 1024,
    kMaxVertex = 1024 * 1024,
    kMaxIndex = 256 * 1024,
    kMaxTokens = 8192,
    kCacheSlots = 32,
    kDirtySlots = 64,
    kReasonCount = 26
};

enum { kEmpty = 0, kAccept = 1, kReject = 2, kNeedsLink = 3 };

static const char *kReasons[kReasonCount] = {
    "not-color", "needs-link", "interface", "mode", "client-index", "mapped", "bounds", "index",
    "nonfinite", "depth", "blend", "stencil", "scissor", "alpha-test", "logic-op", "polygon-smooth",
    "srgb", "clip-plane", "samples", "color-mask", "polygon-mode", "viewport", "cull", "primitive-restart",
    "reference", "provenance"
};

typedef struct {
    const char *p;
    int n;
} Tok;

typedef struct {
    uintptr_t program;
    int state;
    GLint pos_loc;
    GLint color_loc;
    GLint uniform_loc;
    unsigned char vert_hash[CC_SHA256_DIGEST_LENGTH];
    unsigned char frag_hash[CC_SHA256_DIGEST_LENGTH];
    uintptr_t vert_shader;
    uintptr_t frag_shader;
} Slot;

static Slot g_cache[kCacheSlots];
static uintptr_t g_dirty[kDirtySlots];
static int g_dirty_count;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_armed;
static int g_done;
static char g_dir[1024];
static char g_sha[65];
static char g_vert_path[1024];
static char g_frag_path[1024];
static char *g_ref_vert;
static char *g_ref_frag;
static int g_indexed;
static int g_inspected;
static int g_candidates;
static int g_qualified;
static int g_reject_count[kReasonCount];
static int g_forward_count;
static unsigned g_last_mode;
static int g_last_count;
static unsigned g_last_type;
static uint64_t g_last_offset;

static void (*g_real_draw)(GLenum, GLsizei, GLenum, const void *);
static void (*g_real_draw_arb)(GLenum, GLsizei, GLenum, const void *);
static void (*g_real_link)(GLuint);
static void (*g_real_link_arb)(GLhandleARB);
static void (*g_real_delete)(GLuint);
static void (*g_real_delete_arb)(GLhandleARB);
static void (*g_real_source)(GLuint, GLsizei, const GLchar *const *, const GLint *);
static void (*g_real_source_arb)(GLhandleARB, GLsizei, const GLcharARB *const *, const GLint *);

static int tok_eq(Tok a, const char *lit) {
    int n = (int)strlen(lit);
    return a.n == n && memcmp(a.p, lit, (size_t)n) == 0;
}

static int find_seq(const Tok *t, int n, const char *const *words, int wn) {
    for (int i = 0; i + wn <= n; i++) {
        int ok = 1;
        for (int k = 0; k < wn; k++) {
            if (!tok_eq(t[i + k], words[k])) {
                ok = 0;
                break;
            }
        }
        if (ok) {
            return i;
        }
    }
    return -1;
}

static int count_seq(const Tok *t, int n, const char *const *words, int wn) {
    int count = 0;
    for (int i = 0; i + wn <= n; i++) {
        int ok = 1;
        for (int k = 0; k < wn; k++) {
            if (!tok_eq(t[i + k], words[k])) {
                ok = 0;
                break;
            }
        }
        count += ok;
    }
    return count;
}

static void remove_at(Tok *t, int *n, int index, int wn) {
    memmove(t + index, t + index + wn, (size_t)(*n - index - wn) * sizeof(Tok));
    *n -= wn;
}

static int insert_seq(Tok *t, int *n, int index, const char *const *words, int wn) {
    if (*n + wn > kMaxTokens) {
        return 0;
    }
    memmove(t + index + wn, t + index, (size_t)(*n - index) * sizeof(Tok));
    for (int k = 0; k < wn; k++) {
        t[index + k].p = words[k];
        t[index + k].n = (int)strlen(words[k]);
    }
    *n += wn;
    return 1;
}

static int tokenize(const char *s, Tok *t) {
    int n = 0;
    const char *p = s;
    while (*p) {
        unsigned char c = (unsigned char)*p;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
            p++;
            continue;
        }
        if (p[0] == '/' && p[1] == '/') {
            p += 2;
            while (*p && *p != '\n') {
                p++;
            }
            continue;
        }
        if (p[0] == '/' && p[1] == '*') {
            p += 2;
            while (*p && !(p[0] == '*' && p[1] == '/')) {
                p++;
            }
            if (*p) {
                p += 2;
            }
            continue;
        }
        if (n >= kMaxTokens) {
            return -1;
        }
        const char *start = p;
        if (isalpha(c) || c == '_') {
            p++;
            while (isalnum((unsigned char)*p) || *p == '_') {
                p++;
            }
        } else if (isdigit(c) || (c == '.' && isdigit((unsigned char)p[1]))) {
            if (*p == '.') {
                p++;
            }
            while (isdigit((unsigned char)*p)) {
                p++;
            }
            if (*p == '.') {
                p++;
                while (isdigit((unsigned char)*p)) {
                    p++;
                }
            }
            if (*p == 'e' || *p == 'E') {
                const char *e = p + 1;
                if (*e == '+' || *e == '-') {
                    e++;
                }
                if (isdigit((unsigned char)*e)) {
                    p = e;
                    while (isdigit((unsigned char)*p)) {
                        p++;
                    }
                }
            }
        } else {
            p++;
        }
        t[n].p = start;
        t[n].n = (int)(p - start);
        n++;
    }
    return n;
}

static const char *kExt[] = {"#", "extension", "GL_ARB_shader_texture_lod", ":", "enable"};
static const char *kNoLod[] = {"#", "define", "NO_SHADER_TEXTURE_LOD"};
static const char *kPdx[] = {"#", "define", "PDX_OPENGL", "1"};
static const char *kMaps[6][4] = {
    {"#", "define", "MAP_SIZE_X", "5632.000"},
    {"#", "define", "MAP_SIZE_Y", "2048.000"},
    {"#", "define", "MAP_POW2_X", "1.000"},
    {"#", "define", "MAP_POW2_Y", "1.000"},
    {"#", "define", "FOW_POW2_X", "1.000"},
    {"#", "define", "FOW_POW2_Y", "1.000"},
};
static const char *kDeclPos[] = {"attribute", "float2", "in_POSITION", ";"};
static const char *kDeclCol[] = {"attribute", "float4", "in_PDX_COLOR", ";"};
static const char *kDeclVar[] = {"varying", "float4", "v_TEXCOORD1", ";"};

static int rewrite_preamble(Tok *t, int *n) {
    int historical = find_seq(t, *n, kExt, 5) >= 0;
    for (int i = 0; i < 6 && historical; i++) {
        historical = find_seq(t, *n, kMaps[i], 4) >= 0;
    }
    if (!historical) {
        return 1;
    }
    if (find_seq(t, *n, kNoLod, 3) >= 0) {
        return 1;
    }
    int at = find_seq(t, *n, kExt, 5);
    remove_at(t, n, at, 5);
    for (int i = 0; i < 6; i++) {
        at = find_seq(t, *n, kMaps[i], 4);
        if (at < 0) {
            return 0;
        }
        remove_at(t, n, at, 4);
    }
    at = find_seq(t, *n, kPdx, 4);
    int insert = at < 0 ? 0 : at + 4;
    return insert_seq(t, n, insert, kNoLod, 3);
}

static int is_ident(Tok a) {
    return a.n > 0 && (isalpha((unsigned char)a.p[0]) || a.p[0] == '_');
}

static int definition_anchor(const Tok *t, int n) {
    for (int i = 1; i < n; i++) {
        if (tok_eq(t[i], "glsl_main") && is_ident(t[i - 1])) {
            return i - 1;
        }
    }
    return -1;
}

static int remove_one(Tok *t, int *n, const char *const *words, int wn) {
    int at = find_seq(t, *n, words, wn);
    if (at < 0) {
        return 0;
    }
    remove_at(t, n, at, wn);
    return 1;
}

static int place_declarations(Tok *t, int *n, int vertex) {
    if (vertex) {
        if (count_seq(t, *n, kDeclPos, 4) != 1 || count_seq(t, *n, kDeclCol, 4) != 1 ||
            count_seq(t, *n, kDeclVar, 4) != 1) {
            return 0;
        }
        if (!remove_one(t, n, kDeclPos, 4) || !remove_one(t, n, kDeclCol, 4) || !remove_one(t, n, kDeclVar, 4)) {
            return 0;
        }
    } else if (count_seq(t, *n, kDeclVar, 4) != 1 || !remove_one(t, n, kDeclVar, 4)) {
        return 0;
    }
    int anchor = definition_anchor(t, *n);
    if (anchor < 0) {
        return 0;
    }
    if (vertex) {
        if (!insert_seq(t, n, anchor, kDeclPos, 4)) {
            return 0;
        }
        anchor += 4;
        if (!insert_seq(t, n, anchor, kDeclCol, 4)) {
            return 0;
        }
        anchor += 4;
    }
    return insert_seq(t, n, anchor, kDeclVar, 4);
}

static int canonical_equal(const char *source, const char *reference, int vertex) {
    if (source == NULL || reference == NULL) {
        return 0;
    }
    if (strlen(source) > kMaxShader || strlen(reference) > kMaxShader) {
        return 0;
    }
    Tok *a = malloc(sizeof(Tok) * kMaxTokens);
    Tok *b = malloc(sizeof(Tok) * kMaxTokens);
    if (a == NULL || b == NULL) {
        free(a);
        free(b);
        return 0;
    }
    int na = tokenize(source, a);
    int nb = tokenize(reference, b);
    int ok = na >= 0 && nb >= 0 && rewrite_preamble(a, &na) && rewrite_preamble(b, &nb) &&
             place_declarations(a, &na, vertex) && place_declarations(b, &nb, vertex);
    if (ok && na == nb) {
        for (int i = 0; i < na; i++) {
            if (a[i].n != b[i].n || memcmp(a[i].p, b[i].p, (size_t)a[i].n) != 0) {
                ok = 0;
                break;
            }
        }
    } else {
        ok = 0;
    }
    free(a);
    free(b);
    return ok;
}

int eu4_b2_color_vertex_matches(const char *source, const char *reference) {
    return canonical_equal(source, reference, 1);
}

int eu4_b2_color_fragment_matches(const char *source, const char *reference) {
    return canonical_equal(source, reference, 0);
}

int eu4_b2_color_index_range_ok(uint64_t offset, uint64_t count, uint64_t index_size, uint64_t buffer_bytes) {
    if ((index_size != 2 && index_size != 4) || count == 0 || offset > buffer_bytes) {
        return 0;
    }
    if (count > UINT64_MAX / index_size) {
        return 0;
    }
    uint64_t bytes = count * index_size;
    return bytes <= buffer_bytes - offset;
}

int eu4_b2_color_forward_count(void) {
    return g_forward_count;
}

void eu4_b2_color_last_forward(unsigned *mode, int *count, unsigned *type, unsigned long long *offset) {
    if (mode) {
        *mode = g_last_mode;
    }
    if (count) {
        *count = g_last_count;
    }
    if (type) {
        *type = g_last_type;
    }
    if (offset) {
        *offset = g_last_offset;
    }
}

int eu4_b2_color_cached_state(unsigned program) {
    for (int i = 0; i < kCacheSlots; i++) {
        if (g_cache[i].state != kEmpty && g_cache[i].program == program) {
            if (g_cache[i].state == kAccept) {
                return 1;
            }
            if (g_cache[i].state == kReject) {
                return 0;
            }
            return -2;
        }
    }
    return -1;
}

static void sha256_bytes(const void *data, size_t len, unsigned char out[CC_SHA256_DIGEST_LENGTH]) {
    CC_SHA256(data, (CC_LONG)len, out);
}

static void sha256_hex(const void *data, size_t len, char out[65]) {
    unsigned char dig[CC_SHA256_DIGEST_LENGTH];
    sha256_bytes(data, len, dig);
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        sprintf(out + i * 2, "%02x", dig[i]);
    }
    out[64] = 0;
}

static Slot *find_slot(uintptr_t program) {
    for (int i = 0; i < kCacheSlots; i++) {
        if (g_cache[i].state != kEmpty && g_cache[i].program == program) {
            return &g_cache[i];
        }
    }
    return NULL;
}

static Slot *empty_slot(void) {
    for (int i = 0; i < kCacheSlots; i++) {
        if (g_cache[i].state == kEmpty) {
            return &g_cache[i];
        }
    }
    return NULL;
}

static void drop_program(uintptr_t program) {
    Slot *slot = find_slot(program);
    if (slot) {
        memset(slot, 0, sizeof *slot);
    }
}

static void mark_dirty(uintptr_t shader) {
    for (int i = 0; i < g_dirty_count; i++) {
        if (g_dirty[i] == shader) {
            goto linked;
        }
    }
    if (g_dirty_count < kDirtySlots) {
        g_dirty[g_dirty_count++] = shader;
    }
linked:
    for (int i = 0; i < kCacheSlots; i++) {
        if (g_cache[i].state != kEmpty &&
            (g_cache[i].vert_shader == shader || g_cache[i].frag_shader == shader)) {
            g_cache[i].state = kNeedsLink;
        }
    }
}

static int shader_dirty(uintptr_t shader) {
    for (int i = 0; i < g_dirty_count; i++) {
        if (g_dirty[i] == shader) {
            return 1;
        }
    }
    return 0;
}

static void clear_dirty_shader(uintptr_t shader) {
    for (int i = 0; i < g_dirty_count; i++) {
        if (g_dirty[i] == shader) {
            g_dirty[i] = g_dirty[--g_dirty_count];
            return;
        }
    }
}

static void note_reject(const char *reason) {
    for (int i = 0; i < kReasonCount; i++) {
        if (strcmp(kReasons[i], reason) == 0) {
            g_reject_count[i]++;
            return;
        }
    }
}

static void write_counters(void) {
    if (g_dir[0] == 0) {
        return;
    }
    char path[1100];
    char tmp[1100];
    snprintf(path, sizeof path, "%s/counters.json", g_dir);
    snprintf(tmp, sizeof tmp, "%s/counters.json.tmp", g_dir);
    FILE *out = fopen(tmp, "w");
    if (out == NULL) {
        return;
    }
    fprintf(out,
            "{\"indexed_draws\":%d,\"programs_inspected\":%d,\"candidates\":%d,\"qualified\":%d,\"rejects\":{",
            g_indexed, g_inspected, g_candidates, g_qualified);
    int first = 1;
    for (int i = 0; i < kReasonCount; i++) {
        if (g_reject_count[i] == 0) {
            continue;
        }
        fprintf(out, "%s\"%s\":%d", first ? "" : ",", kReasons[i], g_reject_count[i]);
        first = 0;
    }
    fprintf(out, "}}\n");
    fclose(out);
    rename(tmp, path);
}

static char *read_file(const char *path, int *out_len) {
    FILE *in = fopen(path, "rb");
    if (in == NULL) {
        return NULL;
    }
    if (fseek(in, 0, SEEK_END) != 0) {
        fclose(in);
        return NULL;
    }
    long len = ftell(in);
    if (len < 0 || len > kMaxShader || fseek(in, 0, SEEK_SET) != 0) {
        fclose(in);
        return NULL;
    }
    char *data = malloc((size_t)len + 1);
    if (data == NULL || fread(data, 1, (size_t)len, in) != (size_t)len) {
        free(data);
        fclose(in);
        return NULL;
    }
    data[len] = 0;
    fclose(in);
    if (out_len) {
        *out_len = (int)len;
    }
    return data;
}

static int ensure_refs(void) {
    if (g_ref_vert != NULL && g_ref_frag != NULL) {
        return 1;
    }
    g_ref_vert = read_file(g_vert_path, NULL);
    g_ref_frag = read_file(g_frag_path, NULL);
    return g_ref_vert != NULL && g_ref_frag != NULL;
}

static char *shader_text(GLuint shader, int *out_len) {
    GLint length = 0;
    glGetShaderiv(shader, GL_SHADER_SOURCE_LENGTH, &length);
    if (length <= 1 || length > kMaxShader + 1) {
        return NULL;
    }
    char *text = malloc((size_t)length);
    if (text == NULL) {
        return NULL;
    }
    GLsizei got = 0;
    glGetShaderSource(shader, length, &got, text);
    if (got < 0 || got >= length) {
        free(text);
        return NULL;
    }
    text[got] = 0;
    if (out_len) {
        *out_len = got;
    }
    return text;
}

static int uniform_name_ok(const char *name) {
    return strcmp(name, "UNIFORM0") == 0 || strcmp(name, "UNIFORM0[0]") == 0;
}

static const char *evaluate_identity(GLuint program, Slot *slot) {
    if (!ensure_refs()) {
        return "reference";
    }
    GLint attached = 0;
    glGetProgramiv(program, GL_ATTACHED_SHADERS, &attached);
    if (attached != 2) {
        return "not-color";
    }
    GLuint shaders[2] = {0, 0};
    glGetAttachedShaders(program, 2, NULL, shaders);
    GLint types[2] = {0, 0};
    glGetShaderiv(shaders[0], GL_SHADER_TYPE, &types[0]);
    glGetShaderiv(shaders[1], GL_SHADER_TYPE, &types[1]);
    GLuint vert = types[0] == GL_VERTEX_SHADER ? shaders[0] : shaders[1];
    GLuint frag = types[0] == GL_FRAGMENT_SHADER ? shaders[0] : shaders[1];
    if (vert == frag || (types[0] != GL_VERTEX_SHADER && types[0] != GL_FRAGMENT_SHADER)) {
        return "not-color";
    }
    if (shader_dirty(vert) || shader_dirty(frag)) {
        return "needs-link";
    }
    char *vtext = shader_text(vert, NULL);
    char *ftext = shader_text(frag, NULL);
    if (vtext == NULL || ftext == NULL) {
        free(vtext);
        free(ftext);
        return "not-color";
    }
    int match = eu4_b2_color_vertex_matches(vtext, g_ref_vert) && eu4_b2_color_fragment_matches(ftext, g_ref_frag);
    if (match) {
        sha256_bytes(vtext, strlen(vtext), slot->vert_hash);
        sha256_bytes(ftext, strlen(ftext), slot->frag_hash);
    }
    free(vtext);
    free(ftext);
    if (!match) {
        return "not-color";
    }
    GLint attribs = 0;
    GLint uniforms = 0;
    glGetProgramiv(program, GL_ACTIVE_ATTRIBUTES, &attribs);
    glGetProgramiv(program, GL_ACTIVE_UNIFORMS, &uniforms);
    if (attribs != 2 || uniforms != 1) {
        return "interface";
    }
    int saw_pos = 0;
    int saw_color = 0;
    for (GLint i = 0; i < attribs; i++) {
        char name[64];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveAttrib(program, (GLuint)i, (GLsizei)sizeof name, &length, &size, &type, name);
        if (strcmp(name, "in_POSITION") == 0 && type == GL_FLOAT_VEC2 && size == 1) {
            saw_pos = 1;
        } else if (strcmp(name, "in_PDX_COLOR") == 0 && type == GL_FLOAT_VEC4 && size == 1) {
            saw_color = 1;
        }
    }
    char uname[64];
    GLsizei ulen = 0;
    GLint usize = 0;
    GLenum utype = 0;
    glGetActiveUniform(program, 0, (GLsizei)sizeof uname, &ulen, &usize, &utype, uname);
    GLint pos = glGetAttribLocation(program, "in_POSITION");
    GLint color = glGetAttribLocation(program, "in_PDX_COLOR");
    GLint uniform = glGetUniformLocation(program, "UNIFORM0");
    if (!saw_pos || !saw_color || pos < 0 || color < 0 || pos == color || uniform < 0 ||
        !uniform_name_ok(uname) || utype != GL_FLOAT_VEC4 || usize != 4) {
        return "interface";
    }
    slot->pos_loc = pos;
    slot->color_loc = color;
    slot->uniform_loc = uniform;
    slot->vert_shader = vert;
    slot->frag_shader = frag;
    return NULL;
}

static int store_identity(GLuint program, const char *reason, Slot *filled) {
    Slot *slot = find_slot(program);
    if (slot == NULL) {
        slot = empty_slot();
    }
    if (slot == NULL) {
        return 0;
    }
    if (slot->state == kEmpty) {
        g_inspected++;
    }
    memset(slot, 0, sizeof *slot);
    slot->program = program;
    if (reason == NULL) {
        slot->state = kAccept;
        *slot = *filled;
        slot->program = program;
        slot->state = kAccept;
        g_candidates++;
    } else if (strcmp(reason, "needs-link") == 0) {
        slot->state = kNeedsLink;
        slot->vert_shader = filled->vert_shader;
        slot->frag_shader = filled->frag_shader;
    } else if (strcmp(reason, "reference") != 0) {
        slot->state = kReject;
    } else {
        slot->state = kEmpty;
    }
    return 1;
}

static int attrib_format(GLint loc, GLint *size, GLenum *type, GLint *norm, GLint *stride, GLint *buffer,
                         void **pointer) {
    GLint enabled = 0;
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_ENABLED, &enabled);
    if (!enabled) {
        return 0;
    }
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_SIZE, size);
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_TYPE, (GLint *)type);
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_NORMALIZED, norm);
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_STRIDE, stride);
    glGetVertexAttribiv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, buffer);
    glGetVertexAttribPointerv((GLuint)loc, GL_VERTEX_ATTRIB_ARRAY_POINTER, pointer);
    return 1;
}

static int index_fits_vertices(const unsigned char *indices, GLsizei count, GLenum type, uint64_t vertex_bytes) {
    for (GLsizei i = 0; i < count; i++) {
        uint32_t index = type == GL_UNSIGNED_SHORT ? ((const uint16_t *)indices)[i] : ((const uint32_t *)indices)[i];
        if ((uint64_t)index > UINT64_MAX / 12) {
            return 0;
        }
        uint64_t start = (uint64_t)index * 12;
        if (start > vertex_bytes || 12 > vertex_bytes - start) {
            return 0;
        }
    }
    return 1;
}

static const char *state_reason(GLint *viewport, int *dither) {
    if (glIsEnabled(GL_DEPTH_TEST)) {
        return "depth";
    }
    if (glIsEnabled(GL_BLEND)) {
        return "blend";
    }
    if (glIsEnabled(GL_STENCIL_TEST)) {
        return "stencil";
    }
    if (glIsEnabled(GL_SCISSOR_TEST)) {
        return "scissor";
    }
    if (glIsEnabled(GL_ALPHA_TEST)) {
        return "alpha-test";
    }
    if (glIsEnabled(GL_COLOR_LOGIC_OP)) {
        return "logic-op";
    }
    if (glIsEnabled(GL_POLYGON_SMOOTH)) {
        return "polygon-smooth";
    }
    const char *ext = (const char *)glGetString(GL_EXTENSIONS);
    if (ext != NULL && (strstr(ext, "GL_EXT_framebuffer_sRGB") != NULL || strstr(ext, "GL_ARB_framebuffer_sRGB") != NULL) &&
        glIsEnabled(GL_FRAMEBUFFER_SRGB)) {
        return "srgb";
    }
    for (int i = 0; i < 6; i++) {
        if (glIsEnabled(GL_CLIP_PLANE0 + i)) {
            return "clip-plane";
        }
    }
    GLint samples = 0;
    glGetIntegerv(GL_SAMPLE_BUFFERS, &samples);
    if (samples != 0) {
        return "samples";
    }
    GLboolean mask[4] = {0, 0, 0, 0};
    glGetBooleanv(GL_COLOR_WRITEMASK, mask);
    if (!mask[0] || !mask[1] || !mask[2] || !mask[3]) {
        return "color-mask";
    }
    GLint polygon[2] = {0, 0};
    glGetIntegerv(GL_POLYGON_MODE, polygon);
    if (polygon[0] != GL_FILL || polygon[1] != GL_FILL) {
        return "polygon-mode";
    }
    glGetIntegerv(GL_VIEWPORT, viewport);
    if (viewport[0] != 0 || viewport[1] != 0 || viewport[2] < 1 || viewport[2] > 2048 || viewport[3] < 1 ||
        viewport[3] > 2048) {
        return "viewport";
    }
    GLint cull_mode = 0;
    GLint front = 0;
    if (!glIsEnabled(GL_CULL_FACE)) {
        return "cull";
    }
    glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &front);
    if (cull_mode != GL_BACK || front != GL_CW) {
        return "cull";
    }
    const char *version = (const char *)glGetString(GL_VERSION);
    int major = 0;
    if (version != NULL) {
        sscanf(version, "%d", &major);
    }
    if (major >= 3 && glIsEnabled(GL_PRIMITIVE_RESTART)) {
        return "primitive-restart";
    }
    *dither = glIsEnabled(GL_DITHER) ? 1 : 0;
    return NULL;
}

static int write_all(const char *path, const void *data, size_t len) {
    int fd = open(path, O_CREAT | O_TRUNC | O_WRONLY, 0644);
    if (fd < 0) {
        return 0;
    }
    const char *bytes = data;
    size_t left = len;
    while (left > 0) {
        ssize_t n = write(fd, bytes, left);
        if (n < 0) {
            close(fd);
            return 0;
        }
        bytes += n;
        left -= (size_t)n;
    }
    int ok = fsync(fd) == 0;
    close(fd);
    return ok;
}

static int publish_fixture(GLuint program, GLsizei count, GLenum index_type, uint64_t index_offset, GLint pos_loc,
                           GLint color_loc, const void *vertices, GLint vertex_bytes, const void *indices,
                           GLint index_bytes, const float matrix[16], const GLint viewport[4], int dither,
                           const char *vert, const char *frag) {
    if (strlen(g_sha) != 64) {
        return 0;
    }
    char partial[1100];
    char final_dir[1100];
    snprintf(partial, sizeof partial, "%s/fixture.partial", g_dir);
    snprintf(final_dir, sizeof final_dir, "%s/fixture", g_dir);
    if (mkdir(partial, 0755) != 0) {
        return 0;
    }
    char path[1200];
    snprintf(path, sizeof path, "%s/vertices.bin", partial);
    if (!write_all(path, vertices, (size_t)vertex_bytes)) {
        return 0;
    }
    snprintf(path, sizeof path, "%s/indices.bin", partial);
    if (!write_all(path, indices, (size_t)index_bytes)) {
        return 0;
    }
    snprintf(path, sizeof path, "%s/uniform0.bin", partial);
    if (!write_all(path, matrix, sizeof(float) * 16)) {
        return 0;
    }
    snprintf(path, sizeof path, "%s/draw.vert.glsl", partial);
    if (!write_all(path, vert, strlen(vert))) {
        return 0;
    }
    snprintf(path, sizeof path, "%s/draw.frag.glsl", partial);
    if (!write_all(path, frag, strlen(frag))) {
        return 0;
    }
    char hv[65], hi[65], hu[65], hsv[65], hsf[65];
    sha256_hex(vertices, (size_t)vertex_bytes, hv);
    sha256_hex(indices, (size_t)index_bytes, hi);
    sha256_hex(matrix, sizeof(float) * 16, hu);
    sha256_hex(vert, strlen(vert), hsv);
    sha256_hex(frag, strlen(frag), hsf);
    const char *index_name = index_type == GL_UNSIGNED_SHORT ? "GL_UNSIGNED_SHORT" : "GL_UNSIGNED_INT";
    char json[4096];
    int wrote = snprintf(
        json, sizeof json,
        "{\n"
        "  \"schema_version\": 1,\n"
        "  \"capture_version\": 1,\n"
        "  \"executable_sha256\": \"%s\",\n"
        "  \"program_id\": %u,\n"
        "  \"primitive\": \"GL_TRIANGLES\",\n"
        "  \"index_type\": \"%s\",\n"
        "  \"count\": %d,\n"
        "  \"index_offset\": %llu,\n"
        "  \"vertex_bytes\": %d,\n"
        "  \"index_bytes\": %d,\n"
        "  \"stride\": 12,\n"
        "  \"attributes\": [\n"
        "    {\"name\": \"in_POSITION\", \"location\": %d, \"shader_type\": \"GL_FLOAT_VEC2\", \"size\": 2, "
        "\"component_type\": \"GL_FLOAT\", \"normalized\": false, \"offset\": 0},\n"
        "    {\"name\": \"in_PDX_COLOR\", \"location\": %d, \"shader_type\": \"GL_FLOAT_VEC4\", \"size\": 4, "
        "\"component_type\": \"GL_UNSIGNED_BYTE\", \"normalized\": true, \"offset\": 8}\n"
        "  ],\n"
        "  \"viewport\": {\"x\": 0, \"y\": 0, \"width\": %d, \"height\": %d},\n"
        "  \"cull\": true,\n"
        "  \"cull_face\": \"GL_BACK\",\n"
        "  \"front_face\": \"GL_CW\",\n"
        "  \"dither\": %s,\n"
        "  \"depth_test\": false,\n"
        "  \"blend\": false,\n"
        "  \"stencil_test\": false,\n"
        "  \"scissor_test\": false,\n"
        "  \"alpha_test\": false,\n"
        "  \"color_logic_op\": false,\n"
        "  \"framebuffer_srgb\": false,\n"
        "  \"polygon_smooth\": false,\n"
        "  \"clip_planes\": false,\n"
        "  \"primitive_restart\": false,\n"
        "  \"samples\": 0,\n"
        "  \"color_mask\": [true, true, true, true],\n"
        "  \"polygon_mode\": \"GL_FILL\",\n"
        "  \"clear\": [16, 32, 48, 255],\n"
        "  \"gl_format\": \"RGBA8\",\n"
        "  \"metal_format\": \"BGRA8Unorm\",\n"
        "  \"comparison\": \"RGBA_top_down\",\n"
        "  \"acceptance\": {\"coverage_mismatches\": 0, \"max_channel_delta\": 1, \"synthetic_exact\": true},\n"
        "  \"hashes\": {\n"
        "    \"vertices.bin\": \"%s\",\n"
        "    \"indices.bin\": \"%s\",\n"
        "    \"uniform0.bin\": \"%s\",\n"
        "    \"draw.vert.glsl\": \"%s\",\n"
        "    \"draw.frag.glsl\": \"%s\"\n"
        "  }\n"
        "}\n",
        g_sha, program, index_name, count, (unsigned long long)index_offset, vertex_bytes, index_bytes, pos_loc,
        color_loc, viewport[2], viewport[3], dither ? "true" : "false", hv, hi, hu, hsv, hsf);
    if (wrote < 0 || wrote >= (int)sizeof json) {
        return 0;
    }
    snprintf(path, sizeof path, "%s/fixture.json", partial);
    if (!write_all(path, json, (size_t)wrote)) {
        return 0;
    }
    if (rename(partial, final_dir) != 0) {
        return 0;
    }
    return 1;
}

static void discard_partial(void) {
    char partial[1100];
    snprintf(partial, sizeof partial, "%s/fixture.partial", g_dir);
    const char *names[] = {"vertices.bin", "indices.bin", "uniform0.bin", "draw.vert.glsl", "draw.frag.glsl",
                           "fixture.json"};
    for (int i = 0; i < 6; i++) {
        char path[1200];
        snprintf(path, sizeof path, "%s/%s", partial, names[i]);
        unlink(path);
    }
    rmdir(partial);
}

static void inspect(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    if (!g_armed || g_done) {
        return;
    }
    g_indexed++;
    if ((g_indexed % 5000) == 0) {
        write_counters();
    }
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    if (program == 0) {
        return;
    }
    Slot *cached = find_slot((uintptr_t)program);
    Slot filled;
    memset(&filled, 0, sizeof filled);
    if (cached != NULL && cached->state == kNeedsLink) {
        note_reject("needs-link");
        return;
    }
    if (cached != NULL && cached->state == kReject) {
        note_reject("not-color");
        return;
    }
    if (cached == NULL || cached->state != kAccept) {
        const char *reason = evaluate_identity((GLuint)program, &filled);
        if (reason != NULL) {
            store_identity((GLuint)program, reason, &filled);
            note_reject(reason);
            return;
        }
        if (!store_identity((GLuint)program, NULL, &filled)) {
            cached = NULL;
        } else {
            cached = find_slot((uintptr_t)program);
        }
    } else {
        filled = *cached;
    }
    GLint saved_array = 0;
    GLint saved_element = 0;
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &saved_array);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &saved_element);
    const char *reason = NULL;
    void *vertex_copy = NULL;
    void *index_copy = NULL;
    char *vtext = NULL;
    char *ftext = NULL;
    if (filled.vert_shader != 0 && filled.frag_shader != 0) {
        vtext = shader_text((GLuint)filled.vert_shader, NULL);
        ftext = shader_text((GLuint)filled.frag_shader, NULL);
        if (vtext == NULL || ftext == NULL) {
            reason = "needs-link";
        } else if (cached != NULL && cached->state == kAccept) {
            unsigned char vh[CC_SHA256_DIGEST_LENGTH];
            unsigned char fh[CC_SHA256_DIGEST_LENGTH];
            sha256_bytes(vtext, strlen(vtext), vh);
            sha256_bytes(ftext, strlen(ftext), fh);
            GLint pos_now = glGetAttribLocation((GLuint)program, "in_POSITION");
            GLint color_now = glGetAttribLocation((GLuint)program, "in_PDX_COLOR");
            if (memcmp(vh, cached->vert_hash, sizeof vh) != 0 || memcmp(fh, cached->frag_hash, sizeof fh) != 0 ||
                pos_now != cached->pos_loc || color_now != cached->color_loc) {
                reason = "needs-link";
                cached->state = kNeedsLink;
            }
        }
    }
    if (reason == NULL && (mode != GL_TRIANGLES || count < 3 || (count % 3) != 0)) {
        reason = "mode";
    }
    uint64_t index_size = type == GL_UNSIGNED_SHORT ? 2 : type == GL_UNSIGNED_INT ? 4 : 0;
    uint64_t index_offset = (uint64_t)(uintptr_t)indices;
    if (reason == NULL && index_size == 0) {
        reason = "mode";
    }
    if (reason == NULL && saved_element == 0) {
        reason = "client-index";
    }
    GLint pos_size = 0, color_size = 0, pos_norm = 0, color_norm = 0, pos_stride = 0, color_stride = 0;
    GLint pos_buffer = 0, color_buffer = 0;
    GLenum pos_type = 0, color_type = 0;
    void *pos_pointer = NULL;
    void *color_pointer = NULL;
    GLint vertex_bytes = 0;
    GLint index_bytes = 0;
    if (reason == NULL &&
        !attrib_format(filled.pos_loc, &pos_size, &pos_type, &pos_norm, &pos_stride, &pos_buffer, &pos_pointer)) {
        reason = "interface";
    }
    if (reason == NULL && !attrib_format(filled.color_loc, &color_size, &color_type, &color_norm, &color_stride,
                                         &color_buffer, &color_pointer)) {
        reason = "interface";
    }
    if (reason == NULL && (pos_buffer == 0 || pos_buffer != color_buffer || pos_size != 2 || color_size != 4 ||
                           pos_type != GL_FLOAT || color_type != GL_UNSIGNED_BYTE || pos_norm || !color_norm ||
                           pos_stride != 12 || color_stride != 12 || pos_pointer != NULL ||
                           (uintptr_t)color_pointer != 8)) {
        reason = "interface";
    }
    if (reason == NULL) {
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)pos_buffer);
        GLint mapped = 0;
        glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_MAPPED, &mapped);
        glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_SIZE, &vertex_bytes);
        if (mapped) {
            reason = "mapped";
        } else if (vertex_bytes <= 0 || vertex_bytes > kMaxVertex) {
            reason = "bounds";
        }
    }
    if (reason == NULL) {
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)saved_element);
        GLint mapped = 0;
        glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_MAPPED, &mapped);
        glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE, &index_bytes);
        if (mapped) {
            reason = "mapped";
        } else if (index_bytes <= 0 || index_bytes > kMaxIndex ||
                   !eu4_b2_color_index_range_ok(index_offset, (uint64_t)count, index_size, (uint64_t)index_bytes)) {
            reason = "bounds";
        }
    }
    if (reason == NULL) {
        vertex_copy = malloc((size_t)vertex_bytes);
        index_copy = malloc((size_t)index_bytes);
        if (vertex_copy == NULL || index_copy == NULL) {
            reason = "bounds";
        } else {
            glBindBuffer(GL_ARRAY_BUFFER, (GLuint)pos_buffer);
            glGetBufferSubData(GL_ARRAY_BUFFER, 0, vertex_bytes, vertex_copy);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)saved_element);
            glGetBufferSubData(GL_ELEMENT_ARRAY_BUFFER, 0, index_bytes, index_copy);
            if (!index_fits_vertices((const unsigned char *)index_copy + index_offset, count, type,
                                     (uint64_t)vertex_bytes)) {
                reason = "index";
            }
        }
    }
    float matrix[16];
    if (reason == NULL) {
        for (int i = 0; i < 4; i++) {
            char name[32];
            snprintf(name, sizeof name, "UNIFORM0[%d]", i);
            GLint element = glGetUniformLocation((GLuint)program, name);
            if (element < 0) {
                reason = "interface";
                break;
            }
            glGetUniformfv((GLuint)program, element, matrix + i * 4);
        }
        for (int i = 0; reason == NULL && i < 16; i++) {
            if (!isfinite(matrix[i])) {
                reason = "nonfinite";
            }
        }
    }
    GLint viewport[4] = {0, 0, 0, 0};
    int dither = 0;
    if (reason == NULL) {
        reason = state_reason(viewport, &dither);
    }
    if (reason == NULL) {
        if (vtext == NULL || ftext == NULL) {
            reason = "needs-link";
        } else if (!publish_fixture((GLuint)program, count, type, index_offset, filled.pos_loc, filled.color_loc,
                                    vertex_copy, vertex_bytes, index_copy, index_bytes, matrix, viewport, dither, vtext,
                                    ftext)) {
            discard_partial();
            reason = strlen(g_sha) == 64 ? "bounds" : "provenance";
        } else {
            g_qualified++;
            g_done = 1;
        }
    }
    if (reason != NULL && !g_done) {
        note_reject(reason);
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)saved_array);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)saved_element);
    free(vertex_copy);
    free(index_copy);
    free(vtext);
    free(ftext);
    if (g_done || (g_indexed % 5000) == 0) {
        write_counters();
    }
}

static void forward_draw(void (*real)(GLenum, GLsizei, GLenum, const void *), GLenum mode, GLsizei count, GLenum type,
                         const void *indices) {
    g_last_mode = mode;
    g_last_count = count;
    g_last_type = type;
    g_last_offset = (uint64_t)(uintptr_t)indices;
    g_forward_count++;
    if (real == NULL) {
        glDrawElements(mode, count, type, indices);
    } else {
        real(mode, count, type, indices);
    }
    pthread_mutex_lock(&g_lock);
    inspect(mode, count, type, indices);
    pthread_mutex_unlock(&g_lock);
}

static void draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    forward_draw(g_real_draw, mode, count, type, indices);
}

static void draw_elements_arb(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    forward_draw(g_real_draw_arb != NULL ? g_real_draw_arb : g_real_draw, mode, count, type, indices);
}

static void after_link(uintptr_t program) {
    GLint attached = 0;
    glGetProgramiv((GLuint)program, GL_ATTACHED_SHADERS, &attached);
    if (attached > 0 && attached <= 8) {
        GLuint shaders[8];
        GLsizei count = 0;
        glGetAttachedShaders((GLuint)program, 8, &count, shaders);
        for (GLsizei i = 0; i < count; i++) {
            clear_dirty_shader(shaders[i]);
        }
    }
    drop_program(program);
}

static void link_program_hook(GLuint program) {
    if (g_real_link) {
        g_real_link(program);
    }
    pthread_mutex_lock(&g_lock);
    after_link(program);
    pthread_mutex_unlock(&g_lock);
}

static void link_program_arb_hook(GLhandleARB program) {
    if (g_real_link_arb) {
        g_real_link_arb(program);
    }
    pthread_mutex_lock(&g_lock);
    after_link((uintptr_t)program);
    pthread_mutex_unlock(&g_lock);
}

static void delete_program_hook(GLuint program) {
    if (g_real_delete) {
        g_real_delete(program);
    }
    pthread_mutex_lock(&g_lock);
    drop_program(program);
    pthread_mutex_unlock(&g_lock);
}

static void delete_object_arb_hook(GLhandleARB object) {
    if (g_real_delete_arb) {
        g_real_delete_arb(object);
    }
    pthread_mutex_lock(&g_lock);
    drop_program((uintptr_t)object);
    clear_dirty_shader((uintptr_t)object);
    pthread_mutex_unlock(&g_lock);
}

static void shader_source_hook(GLuint shader, GLsizei count, const GLchar *const *string, const GLint *length) {
    if (g_real_source) {
        g_real_source(shader, count, string, length);
    }
    pthread_mutex_lock(&g_lock);
    mark_dirty(shader);
    pthread_mutex_unlock(&g_lock);
}

static void shader_source_arb_hook(GLhandleARB shader, GLsizei count, const GLcharARB *const *string,
                                   const GLint *length) {
    if (g_real_source_arb) {
        g_real_source_arb(shader, count, string, length);
    }
    pthread_mutex_lock(&g_lock);
    mark_dirty((uintptr_t)shader);
    pthread_mutex_unlock(&g_lock);
}

static void *hooked_dlsym(void *handle, const char *symbol) {
    void *found = dlsym(handle, symbol);
    if (symbol == NULL) {
        return found;
    }
    if (strcmp(symbol, "glDrawElements") == 0) {
        g_real_draw = found;
        return (void *)draw_elements;
    }
    if (strcmp(symbol, "glDrawElementsARB") == 0) {
        g_real_draw_arb = found;
        return (void *)draw_elements_arb;
    }
    if (strcmp(symbol, "glLinkProgram") == 0) {
        g_real_link = found;
        return (void *)link_program_hook;
    }
    if (strcmp(symbol, "glLinkProgramARB") == 0) {
        g_real_link_arb = found;
        return (void *)link_program_arb_hook;
    }
    if (strcmp(symbol, "glDeleteProgram") == 0) {
        g_real_delete = found;
        return (void *)delete_program_hook;
    }
    if (strcmp(symbol, "glDeleteObjectARB") == 0) {
        g_real_delete_arb = found;
        return (void *)delete_object_arb_hook;
    }
    if (strcmp(symbol, "glShaderSource") == 0) {
        g_real_source = found;
        return (void *)shader_source_hook;
    }
    if (strcmp(symbol, "glShaderSourceARB") == 0) {
        g_real_source_arb = found;
        return (void *)shader_source_arb_hook;
    }
    return found;
}

#define INTERPOSE(repl, orig) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##orig = {(const void *)(repl), (const void *)(orig)}

INTERPOSE(hooked_dlsym, dlsym);

__attribute__((constructor)) static void eu4_b2_color_init(void) {
    const char *dir = getenv("EU4_B2_COLOR_DIR");
    const char *sha = getenv("EU4_B2_COLOR_EXE_SHA256");
    const char *vert = getenv("EU4_B2_COLOR_VERT");
    const char *frag = getenv("EU4_B2_COLOR_FRAG");
    if (dir != NULL && dir[0] == '/' && strlen(dir) < sizeof g_dir) {
        snprintf(g_dir, sizeof g_dir, "%s", dir);
        g_armed = 1;
    }
    if (sha != NULL && strlen(sha) == 64) {
        snprintf(g_sha, sizeof g_sha, "%s", sha);
    }
    if (vert != NULL && strlen(vert) < sizeof g_vert_path) {
        snprintf(g_vert_path, sizeof g_vert_path, "%s", vert);
    }
    if (frag != NULL && strlen(frag) < sizeof g_frag_path) {
        snprintf(g_frag_path, sizeof g_frag_path, "%s", frag);
    }
}

__attribute__((destructor)) static void eu4_b2_color_fini(void) {
    write_counters();
}
