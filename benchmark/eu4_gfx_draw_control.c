/* One-draw original-OpenGL control executor for the M7 qualification.
 *
 * This is the smallest renderer that can reproduce the selected default-
 * framebuffer draw from the restricted replay-input package: it creates a GL
 * target whose queried properties match the captured drawable, compiles the
 * captured GLSL program, restores the captured typed buffers, compressed
 * texture blocks, sampler state, uniforms and render state, seeds the
 * authorised pre-draw colour/depth/stencil state, executes exactly one draw
 * through the original OpenGL API, and reads the result back as BGRA8.
 *
 * It never reads the live post-draw image, the frame directory, Metal output
 * or a game-file texture.  Its only inputs are the package manifest and the
 * normalised control file written by the reviewed Python driver.
 *
 * When a captured property cannot be reproduced (multisampling, non-uniform
 * stencil seed, unsupported format/state) the executor writes an explicit
 * unsupported_drawable execution record; it never changes precision,
 * postprocesses the image or substitutes a different target to force a match.
 */
#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CTL_MAX 4096
#define CTL_PATH 1024
#define CTL_NAME 128
#define MAX_ATTRIBUTES 16
#define MAX_UNIFORMS 64
#define MAX_TEXTURES 32
#define MAX_LEVELS 512
#define MAX_MISSING 32

#ifndef GL_FRAMEBUFFER_SRGB
#define GL_FRAMEBUFFER_SRGB 0x8DB9
#endif
#ifndef GL_TEXTURE_MAX_ANISOTROPY_EXT
#define GL_TEXTURE_MAX_ANISOTROPY_EXT 0x84FE
#endif
#ifndef GL_DEPTH_COMPONENT32
#define GL_DEPTH_COMPONENT32 0x81A7
#endif
#ifndef GL_RGBA16F
#define GL_RGBA16F 0x881A
#define GL_RGBA32F 0x8814
#define GL_RGB16F 0x881B
#define GL_RGB32F 0x8815
#endif
#ifndef GL_COMPRESSED_RGB_S3TC_DXT1_EXT
#define GL_COMPRESSED_RGB_S3TC_DXT1_EXT 0x83F0
#define GL_COMPRESSED_RGBA_S3TC_DXT1_EXT 0x83F1
#define GL_COMPRESSED_RGBA_S3TC_DXT3_EXT 0x83F2
#define GL_COMPRESSED_RGBA_S3TC_DXT5_EXT 0x83F3
#endif

typedef struct {
    int index;
    char name[CTL_NAME];
    int enabled;
    int components;
    unsigned type;
    int normalized;
    int integer;
    int stride;
    int offset;
    int divisor;
    char path[CTL_PATH];
} attribute_record;

typedef struct {
    char name[CTL_NAME];
    char type[32];
    int components;
    int elements;
    char path[CTL_PATH];
} uniform_record;

typedef struct {
    int unit;
    int level;
    int width;
    int height;
    int compressed;
    unsigned internal_format;
    long bytes;
    int row_stride;
    char path[CTL_PATH];
} texture_level;

typedef struct {
    int unit;
    unsigned target;
    int level_count;
    unsigned min_filter;
    unsigned mag_filter;
    unsigned wrap_s;
    unsigned wrap_t;
    unsigned wrap_r;
    float min_lod;
    float max_lod;
    float lod_bias;
    int base_level;
    int max_level;
    unsigned compare_mode;
    unsigned compare_func;
    int anisotropy;
    char sampler[CTL_NAME];
} texture_unit;

typedef struct {
    char name[CTL_NAME];
    int unit;
} sampler_binding;

typedef struct {
    char reference[CTL_PATH];
    unsigned target;
    int width;
    int height;
    int drawable_samples;
    int drawable_sample_buffers;
    int drawable_depth_bits;
    int drawable_stencil_bits;
    int drawable_red_bits;
    int drawable_green_bits;
    int drawable_blue_bits;
    int drawable_alpha_bits;
    int drawable_srgb;
    int drawable_srgb_known;
    int drawable_double_buffer;
    int drawable_width;
    int drawable_height;
    int src_read_buffer;
    int src_draw_buffer;
    int state_srgb;
    int state_srgb_known;
    int state_dither;
    int state_multisample;
    int state_logic_op_enabled;
    unsigned state_logic_op;
    int color_seed_is_file;
    char color_seed[CTL_PATH];
    int depth_seed_is_file;
    double depth_clear;
    char depth_seed[CTL_PATH];
    int stencil_seed_is_file;
    int stencil_clear;
    char stencil_seed[CTL_PATH];
    int indexed;
    unsigned mode;
    int first;
    int count;
    int first_index;
    int base_vertex;
    int instance_count;
    int stride_guess;
    char index_path[CTL_PATH];
    char vertex_shader[CTL_PATH];
    char pixel_shader[CTL_PATH];
    char out[CTL_PATH];
    char failed[256];
    char missing[MAX_MISSING][256];
    int missing_count;
    int unsupported;
    attribute_record attributes[MAX_ATTRIBUTES];
    int attribute_count;
    uniform_record uniforms[MAX_UNIFORMS];
    int uniform_count;
    sampler_binding samplers[MAX_UNIFORMS];
    int sampler_count;
    texture_unit textures[MAX_TEXTURES];
    int texture_count;
    texture_level levels[MAX_LEVELS];
    int level_count;
    unsigned blend_src_rgb;
    unsigned blend_dst_rgb;
    unsigned blend_src_alpha;
    unsigned blend_dst_alpha;
    unsigned blend_eq_rgb;
    unsigned blend_eq_alpha;
    int blend_enabled;
    int color_mask[4];
    int depth_test;
    unsigned depth_func;
    int depth_write;
    float depth_range[2];
    int stencil_test;
    unsigned stencil_func;
    int stencil_ref;
    int stencil_value_mask;
    unsigned stencil_fail;
    unsigned stencil_zfail;
    unsigned stencil_zpass;
    int stencil_write_mask;
    int cull_enabled;
    unsigned cull_mode;
    unsigned front_face;
    int polygon_mode[2];
    int polygon_mode_seen;
    int polygon_offset_fill;
    float polygon_offset_factor;
    float polygon_offset_units;
    int alpha_test;
    unsigned alpha_func;
    float alpha_ref;
    int scissor_test;
    int scissor_box[4];
    int viewport[4];
} control_request;

static control_request g;

static void die(const char *message, const char *detail) {
    if (detail != NULL && detail[0] != 0) {
        fprintf(stderr, "draw_control: %s: %s\n", message, detail);
    } else {
        fprintf(stderr, "draw_control: %s\n", message);
    }
    exit(2);
}

static void *xmalloc(size_t size) {
    void *value = malloc(size ? size : 1);
    if (value == NULL) {
        die("out of memory", NULL);
    }
    return value;
}

static unsigned char *read_file(const char *path, long *size_out) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        die("cannot open input", path);
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        die("cannot seek input", path);
    }
    long size = ftell(file);
    if (size < 0) {
        die("cannot size input", path);
    }
    rewind(file);
    unsigned char *body = xmalloc((size_t)size + 1);
    if (size > 0 && fread(body, 1, (size_t)size, file) != (size_t)size) {
        die("short read", path);
    }
    fclose(file);
    body[size] = 0;
    *size_out = size;
    return body;
}

static void write_file(const char *path, const void *body, size_t size) {
    FILE *file = fopen(path, "wb");
    if (file == NULL) {
        die("cannot write output", path);
    }
    if (size > 0 && fwrite(body, 1, size, file) != size) {
        die("short write", path);
    }
    if (fclose(file) != 0) {
        die("cannot close output", path);
    }
}

static int hex_value(const char *text) {
    int value = 0;
    if (text == NULL) return 0;
    if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) {
        return (int)strtoul(text + 2, NULL, 16);
    }
    value = (int)strtol(text, NULL, 0);
    return value;
}

static void add_missing(const char *text) {
    if (g.missing_count >= MAX_MISSING) return;
    snprintf(g.missing[g.missing_count], sizeof(g.missing[0]), "%s", text);
    g.missing_count++;
    g.unsupported = 1;
}

static unsigned parse_unsigned(const char *text) {
    return (unsigned)hex_value(text);
}

static void copy_path(char *out, size_t size, const char *value) {
    if (value == NULL) {
        out[0] = 0;
        return;
    }
    snprintf(out, size, "%s", value);
}

static void parse_ctl(const char *path) {
    FILE *file = fopen(path, "r");
    if (file == NULL) {
        die("cannot open control file", path);
    }
    memset(&g, 0, sizeof(g));
    g.drawable_samples = -1;
    g.drawable_sample_buffers = -1;
    g.drawable_depth_bits = -1;
    g.drawable_stencil_bits = -1;
    g.drawable_red_bits = -1;
    g.drawable_green_bits = -1;
    g.drawable_blue_bits = -1;
    g.drawable_alpha_bits = -1;
    g.drawable_srgb = -1;
    g.state_srgb = -1;
    g.depth_range[0] = 0.0f;
    g.depth_range[1] = 1.0f;
    g.color_mask[0] = g.color_mask[1] = g.color_mask[2] = g.color_mask[3] = 1;
    g.stencil_value_mask = -1;
    g.stencil_write_mask = -1;
    g.polygon_mode[0] = 0x1B02;
    g.polygon_mode[1] = 0x1B02;
    char line[8192];
    while (fgets(line, sizeof(line), file) != NULL) {
        char *save = NULL;
        char *key = strtok_r(line, " \t\r\n", &save);
        if (key == NULL || key[0] == '#') continue;
        if (strcmp(key, "width") == 0) {
            g.width = atoi(strtok_r(NULL, " \t\r\n", &save));
        } else if (strcmp(key, "height") == 0) {
            g.height = atoi(strtok_r(NULL, " \t\r\n", &save));
        } else if (strcmp(key, "reference") == 0) {
            copy_path(g.reference, sizeof(g.reference),
                      strtok_r(NULL, "\r\n", &save));
        } else if (strcmp(key, "out") == 0) {
            copy_path(g.out, sizeof(g.out), strtok_r(NULL, "\r\n", &save));
        } else if (strcmp(key, "drawable") == 0) {
            char *field = strtok_r(NULL, " \t\r\n", &save);
            char *value = strtok_r(NULL, " \t\r\n", &save);
            if (field == NULL || value == NULL) continue;
            int parsed = hex_value(value);
            if (strcmp(field, "samples") == 0) g.drawable_samples = parsed;
            else if (strcmp(field, "sample_buffers") == 0) g.drawable_sample_buffers = parsed;
            else if (strcmp(field, "depth_bits") == 0) g.drawable_depth_bits = parsed;
            else if (strcmp(field, "stencil_bits") == 0) g.drawable_stencil_bits = parsed;
            else if (strcmp(field, "red_bits") == 0) g.drawable_red_bits = parsed;
            else if (strcmp(field, "green_bits") == 0) g.drawable_green_bits = parsed;
            else if (strcmp(field, "blue_bits") == 0) g.drawable_blue_bits = parsed;
            else if (strcmp(field, "alpha_bits") == 0) g.drawable_alpha_bits = parsed;
            else if (strcmp(field, "srgb") == 0) g.drawable_srgb = parsed;
            else if (strcmp(field, "srgb_known") == 0) g.drawable_srgb_known = parsed;
            else if (strcmp(field, "double_buffer") == 0) g.drawable_double_buffer = parsed;
            else if (strcmp(field, "width") == 0) g.drawable_width = parsed;
            else if (strcmp(field, "height") == 0) g.drawable_height = parsed;
        } else if (strcmp(key, "state") == 0) {
            char *field = strtok_r(NULL, " \t\r\n", &save);
            char *value = strtok_r(NULL, " \t\r\n", &save);
            if (field == NULL || value == NULL) continue;
            int parsed = hex_value(value);
            if (strcmp(field, "srgb") == 0) g.state_srgb = parsed;
            else if (strcmp(field, "srgb_known") == 0) g.state_srgb_known = parsed;
            else if (strcmp(field, "dither") == 0) g.state_dither = parsed;
            else if (strcmp(field, "multisample") == 0) g.state_multisample = parsed;
            else if (strcmp(field, "blend") == 0) g.blend_enabled = parsed;
            else if (strcmp(field, "blend_src_rgb") == 0) g.blend_src_rgb = parse_unsigned(value);
            else if (strcmp(field, "blend_dst_rgb") == 0) g.blend_dst_rgb = parse_unsigned(value);
            else if (strcmp(field, "blend_src_alpha") == 0) g.blend_src_alpha = parse_unsigned(value);
            else if (strcmp(field, "blend_dst_alpha") == 0) g.blend_dst_alpha = parse_unsigned(value);
            else if (strcmp(field, "blend_eq_rgb") == 0) g.blend_eq_rgb = parse_unsigned(value);
            else if (strcmp(field, "blend_eq_alpha") == 0) g.blend_eq_alpha = parse_unsigned(value);
            else if (strcmp(field, "color_mask") == 0) {
                for (int i = 0; i < 4; i++) {
                    char *item = i == 0 ? value : strtok_r(NULL, " \t\r\n", &save);
                    g.color_mask[i] = item ? atoi(item) : 1;
                }
            } else if (strcmp(field, "depth_test") == 0) g.depth_test = parsed;
            else if (strcmp(field, "depth_func") == 0) g.depth_func = parse_unsigned(value);
            else if (strcmp(field, "depth_write") == 0) g.depth_write = parsed;
            else if (strcmp(field, "depth_near") == 0) g.depth_range[0] = (float)atof(value);
            else if (strcmp(field, "depth_far") == 0) g.depth_range[1] = (float)atof(value);
            else if (strcmp(field, "stencil_test") == 0) g.stencil_test = parsed;
            else if (strcmp(field, "stencil_func") == 0) g.stencil_func = parse_unsigned(value);
            else if (strcmp(field, "stencil_ref") == 0) g.stencil_ref = parsed;
            else if (strcmp(field, "stencil_value_mask") == 0) g.stencil_value_mask = parsed;
            else if (strcmp(field, "stencil_fail") == 0) g.stencil_fail = parse_unsigned(value);
            else if (strcmp(field, "stencil_zfail") == 0) g.stencil_zfail = parse_unsigned(value);
            else if (strcmp(field, "stencil_zpass") == 0) g.stencil_zpass = parse_unsigned(value);
            else if (strcmp(field, "stencil_write_mask") == 0) g.stencil_write_mask = parsed;
            else if (strcmp(field, "cull") == 0) g.cull_enabled = parsed;
            else if (strcmp(field, "cull_mode") == 0) g.cull_mode = parse_unsigned(value);
            else if (strcmp(field, "front_face") == 0) g.front_face = parse_unsigned(value);
            else if (strcmp(field, "polygon_mode") == 0) {
                g.polygon_mode[0] = parsed;
                g.polygon_mode[1] = parsed;
                g.polygon_mode_seen = 1;
            }
            else if (strcmp(field, "polygon_mode_front") == 0) {
                g.polygon_mode[0] = parsed;
                g.polygon_mode_seen = 1;
            }
            else if (strcmp(field, "polygon_mode_back") == 0) {
                g.polygon_mode[1] = parsed;
                g.polygon_mode_seen = 1;
            }
            else if (strcmp(field, "polygon_offset_fill") == 0) g.polygon_offset_fill = parsed;
            else if (strcmp(field, "polygon_offset_factor") == 0) g.polygon_offset_factor = (float)atof(value);
            else if (strcmp(field, "polygon_offset_units") == 0) g.polygon_offset_units = (float)atof(value);
            else if (strcmp(field, "alpha_test") == 0) g.alpha_test = parsed;
            else if (strcmp(field, "alpha_func") == 0) g.alpha_func = parse_unsigned(value);
            else if (strcmp(field, "alpha_ref") == 0) g.alpha_ref = (float)atof(value);
            else if (strcmp(field, "scissor") == 0) g.scissor_test = parsed;
            else if (strcmp(field, "scissor_box") == 0) {
                for (int i = 0; i < 4; i++) {
                    char *item = i == 0 ? value : strtok_r(NULL, " \t\r\n", &save);
                    g.scissor_box[i] = item ? atoi(item) : 0;
                }
            } else if (strcmp(field, "viewport") == 0) {
                for (int i = 0; i < 4; i++) {
                    char *item = i == 0 ? value : strtok_r(NULL, " \t\r\n", &save);
                    g.viewport[i] = item ? atoi(item) : 0;
                }
            } else if (strcmp(field, "logic_op") == 0) g.state_logic_op = parse_unsigned(value);
            else if (strcmp(field, "logic_op_enabled") == 0) g.state_logic_op_enabled = parsed;
        } else if (strcmp(key, "seed") == 0) {
            char *label = strtok_r(NULL, " \t\r\n", &save);
            char *source = strtok_r(NULL, " \t\r\n", &save);
            char *value = strtok_r(NULL, "\r\n", &save);
            if (label == NULL || source == NULL) continue;
            if (strcmp(label, "color") == 0) {
                g.color_seed_is_file = strcmp(source, "file") == 0;
                if (g.color_seed_is_file && value != NULL) copy_path(g.color_seed, sizeof(g.color_seed), value);
            } else if (strcmp(label, "depth") == 0) {
                g.depth_seed_is_file = strcmp(source, "file") == 0;
                if (g.depth_seed_is_file) {
                    if (value != NULL) copy_path(g.depth_seed, sizeof(g.depth_seed), value);
                } else if (value != NULL) {
                    g.depth_clear = atof(value);
                }
            } else if (strcmp(label, "stencil") == 0) {
                g.stencil_seed_is_file = strcmp(source, "file") == 0;
                if (g.stencil_seed_is_file) {
                    if (value != NULL) copy_path(g.stencil_seed, sizeof(g.stencil_seed), value);
                } else if (value != NULL) {
                    g.stencil_clear = atoi(value);
                }
            }
        } else if (strcmp(key, "draw") == 0) {
            char *indexed = strtok_r(NULL, " \t\r\n", &save);
            g.indexed = indexed != NULL && strcmp(indexed, "indexed") == 0;
            g.mode = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            g.count = atoi(strtok_r(NULL, " \t\r\n", &save));
            g.first = atoi(strtok_r(NULL, " \t\r\n", &save));
            g.first_index = atoi(strtok_r(NULL, " \t\r\n", &save));
            g.base_vertex = atoi(strtok_r(NULL, " \t\r\n", &save));
            g.instance_count = atoi(strtok_r(NULL, " \t\r\n", &save));
            char *index_path = strtok_r(NULL, "\r\n", &save);
            if (index_path != NULL) copy_path(g.index_path, sizeof(g.index_path), index_path);
        } else if (strcmp(key, "shader") == 0) {
            char *stage = strtok_r(NULL, " \t\r\n", &save);
            char *value = strtok_r(NULL, "\r\n", &save);
            if (stage != NULL && value != NULL) {
                if (strcmp(stage, "vertex") == 0) copy_path(g.vertex_shader, sizeof(g.vertex_shader), value);
                else if (strcmp(stage, "pixel") == 0) copy_path(g.pixel_shader, sizeof(g.pixel_shader), value);
            }
        } else if (strcmp(key, "attrib") == 0) {
            if (g.attribute_count >= MAX_ATTRIBUTES) die("too many attributes", NULL);
            attribute_record *item = &g.attributes[g.attribute_count++];
            item->index = atoi(strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->name, sizeof(item->name), strtok_r(NULL, " \t\r\n", &save));
            item->enabled = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->components = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->type = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->normalized = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->integer = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->stride = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->offset = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->divisor = atoi(strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->path, sizeof(item->path), strtok_r(NULL, "\r\n", &save));
        } else if (strcmp(key, "uniform") == 0) {
            if (g.uniform_count >= MAX_UNIFORMS) die("too many uniforms", NULL);
            uniform_record *item = &g.uniforms[g.uniform_count++];
            copy_path(item->name, sizeof(item->name), strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->type, sizeof(item->type), strtok_r(NULL, " \t\r\n", &save));
            item->components = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->elements = atoi(strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->path, sizeof(item->path), strtok_r(NULL, "\r\n", &save));
        } else if (strcmp(key, "sampler") == 0) {
            if (g.sampler_count >= MAX_UNIFORMS) die("too many sampler bindings", NULL);
            sampler_binding *item = &g.samplers[g.sampler_count++];
            char *name = strtok_r(NULL, " \t\r\n", &save);
            char *unit = strtok_r(NULL, " \t\r\n", &save);
            if (name == NULL || unit == NULL) die("malformed sampler record", NULL);
            copy_path(item->name, sizeof(item->name), name);
            item->unit = atoi(unit);
        } else if (strcmp(key, "texture") == 0) {
            if (g.texture_count >= MAX_TEXTURES) die("too many textures", NULL);
            texture_unit *item = &g.textures[g.texture_count++];
            item->unit = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->target = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->min_filter = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->mag_filter = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->wrap_s = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->wrap_t = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->wrap_r = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->min_lod = (float)atof(strtok_r(NULL, " \t\r\n", &save));
            item->max_lod = (float)atof(strtok_r(NULL, " \t\r\n", &save));
            item->lod_bias = (float)atof(strtok_r(NULL, " \t\r\n", &save));
            item->base_level = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->max_level = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->compare_mode = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->compare_func = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->anisotropy = atoi(strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->sampler, sizeof(item->sampler), strtok_r(NULL, "\r\n", &save));
            item->level_count = 0;
        } else if (strcmp(key, "texlevel") == 0) {
            if (g.level_count >= MAX_LEVELS) die("too many texture levels", NULL);
            texture_level *item = &g.levels[g.level_count++];
            item->unit = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->level = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->width = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->height = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->internal_format = parse_unsigned(strtok_r(NULL, " \t\r\n", &save));
            item->compressed = atoi(strtok_r(NULL, " \t\r\n", &save));
            item->bytes = atol(strtok_r(NULL, " \t\r\n", &save));
            item->row_stride = atoi(strtok_r(NULL, " \t\r\n", &save));
            copy_path(item->path, sizeof(item->path), strtok_r(NULL, "\r\n", &save));
            for (int i = 0; i < g.texture_count; i++) {
                if (g.textures[i].unit == item->unit) g.textures[i].level_count++;
            }
        }
    }
    fclose(file);
}

static GLuint compile_shader(GLenum type, const char *path) {
    long size = 0;
    unsigned char *source = read_file(path, &size);
    GLuint shader = glCreateShader(type);
    const char *text = (const char *)source;
    glShaderSource(shader, 1, &text, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[8192];
        GLsizei length = 0;
        glGetShaderInfoLog(shader, sizeof(log), &length, log);
        fprintf(stderr, "draw_control: shader %s failed:\n%.*s\n", path,
                (int)length, log);
        snprintf(g.failed, sizeof(g.failed), "shader_compile_failed");
        free(source);
        return 0;
    }
    free(source);
    return shader;
}

static int query_target_properties(GLuint fbo, int *samples,
                                   int *sample_buffers, int *depth_bits,
                                   int *stencil_bits, int *rgba_bits) {
    GLint value = 0;
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glGetIntegerv(GL_SAMPLES, &value);
    *samples = (int)value;
    glGetIntegerv(GL_SAMPLE_BUFFERS, &value);
    *sample_buffers = (int)value;
    /* Querying the size of an unattached point can plant a GL error, which
       the execution check would then attribute to the draw. */
    *depth_bits = 0;
    *stencil_bits = 0;
    if (g.drawable_depth_bits > 0) {
        glGetFramebufferAttachmentParameteriv(
            GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
            GL_FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE, &value);
        *depth_bits = (int)value;
    }
    if (g.drawable_stencil_bits > 0) {
        glGetFramebufferAttachmentParameteriv(
            GL_FRAMEBUFFER, GL_STENCIL_ATTACHMENT,
            GL_FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE, &value);
        *stencil_bits = (int)value;
    }
    GLenum attachments[4] = {GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE,
                             GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE,
                             GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE,
                             GL_FRAMEBUFFER_ATTACHMENT_ALPHA_SIZE};
    for (int i = 0; i < 4; i++) {
        glGetFramebufferAttachmentParameteriv(
            GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, attachments[i], &value);
        rgba_bits[i] = (int)value;
    }
    return glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE
        ? 0 : -1;
}

static int uniform_float_components_ok(const char *type, int components,
                                       int elements, long bytes) {
    if (strcmp(type, "float") != 0) return 0;
    if (components < 1 || components > 4 || elements < 1) return 0;
    return bytes == (long)components * (long)elements * 4L;
}

static int all_stencil_equal(const unsigned char *body, long size) {
    if (size <= 0) return -1;
    for (long i = 1; i < size; i++) {
        if (body[i] != body[0]) return -1;
    }
    return (int)body[0];
}

static void json_escape(const char *in, char *out, size_t size) {
    size_t used = 0;
    if (size == 0) return;
    for (const char *cursor = in; cursor != NULL && *cursor != 0; cursor++) {
        unsigned char character = (unsigned char)*cursor;
        if (used + 7 >= size) break;
        if (character == '"' || character == '\\') {
            out[used++] = '\\';
            out[used++] = (char)character;
        } else if (character < 0x20) {
            used += (size_t)snprintf(out + used, size - used, "\\u%04x",
                                     character);
        } else {
            out[used++] = (char)character;
        }
    }
    out[used < size ? used : size - 1] = 0;
}

static void write_unsupported(const char *out_dir) {
    char path[CTL_PATH];
    snprintf(path, sizeof(path), "%s/execution.json", out_dir);
    char buffer[16384];
    int used = snprintf(buffer, sizeof(buffer),
                        "{\"schema\":\"gfx-gl-control-execution/v1\","
                        "\"execution\":\"unsupported_drawable\","
                        "\"missing\":[");
    for (int i = 0; i < g.missing_count && used > 0 &&
                    used < (int)sizeof(buffer); i++) {
        used += snprintf(buffer + used, sizeof(buffer) - (size_t)used,
                         "%s\"%s\"", i == 0 ? "" : ",", g.missing[i]);
    }
    if (used > 0 && used < (int)sizeof(buffer)) {
        snprintf(buffer + used, sizeof(buffer) - (size_t)used, "]}");
    }
    write_file(path, buffer, strlen(buffer));
}

int main(int argc, char **argv) {
    const char *ctl_path = NULL;
    const char *out_dir = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--control") == 0) ctl_path = argv[i + 1];
        else if (strcmp(argv[i], "--out") == 0) out_dir = argv[i + 1];
    }
    if (ctl_path == NULL || out_dir == NULL) {
        fprintf(stderr, "usage: %s --control <ctl> --out <dir>\n", argv[0]);
        return 2;
    }
    parse_ctl(ctl_path);
    if (g.width <= 0 || g.height <= 0 || g.count <= 0) {
        die("invalid control dimensions or draw count", NULL);
    }
    snprintf(g.out, sizeof(g.out), "%s/result.bgra", out_dir);

    if (g.drawable_width != g.width || g.drawable_height != g.height) {
        add_missing("control target extent does not match the captured drawable");
    }
    if (g.drawable_samples > 0 || g.drawable_sample_buffers > 0) {
        add_missing("multisampled default drawable cannot be seeded per sample "
                    "through an original-GL offscreen target");
    }
    if (g.drawable_srgb_known == 0 || g.state_srgb_known == 0 ||
        g.state_srgb < 0) {
        add_missing("the captured drawable's sRGB capability/state is not known");
    }
    if (g.drawable_red_bits != 8 || g.drawable_green_bits != 8 ||
        g.drawable_blue_bits != 8 || g.drawable_alpha_bits != 8) {
        add_missing("the default drawable colour precision is not RGBA8");
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        kCGLPFADepthSize, (CGLPixelFormatAttribute)(g.drawable_depth_bits > 0
                                                    ? g.drawable_depth_bits : 24),
        kCGLPFAStencilSize, (CGLPixelFormatAttribute)8,
        0};
    CGLPixelFormatObj format = NULL;
    GLint format_count = 0;
    if (CGLChoosePixelFormat(attributes, &format, &format_count) != kCGLNoError ||
        format == NULL) {
        add_missing("no legacy OpenGL pixel format is available");
        write_unsupported(out_dir);
        return 0;
    }
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError ||
        context == NULL) {
        add_missing("cannot create an original-OpenGL context");
        write_unsupported(out_dir);
        return 0;
    }
    CGLSetCurrentContext(context);

    const char *vendor = (const char *)glGetString(GL_VENDOR);
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *version = (const char *)glGetString(GL_VERSION);
    if (vendor == NULL || renderer == NULL || version == NULL) {
        add_missing("the original-OpenGL driver identity is unavailable");
        write_unsupported(out_dir);
        return 0;
    }

    GLuint color_texture = 0;
    glGenTextures(1, &color_texture);
    glBindTexture(GL_TEXTURE_2D, color_texture);
    GLenum color_internal = g.state_srgb ? GL_SRGB8_ALPHA8 : GL_RGBA8;
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, (GLint)color_internal, g.width, g.height, 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, NULL);

    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, color_texture, 0);

    GLuint depth_texture = 0;
    if (g.drawable_depth_bits > 0) {
        glGenTextures(1, &depth_texture);
        glBindTexture(GL_TEXTURE_2D, depth_texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_COMPARE_MODE, GL_NONE);
        GLenum depth_internal = GL_DEPTH_COMPONENT24;
        if (g.drawable_depth_bits == 32) depth_internal = GL_DEPTH_COMPONENT32;
        else if (g.drawable_depth_bits == 16) depth_internal = GL_DEPTH_COMPONENT16;
        glTexImage2D(GL_TEXTURE_2D, 0, (GLint)depth_internal, g.width, g.height,
                     0, GL_DEPTH_COMPONENT, GL_FLOAT, NULL);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                               GL_TEXTURE_2D, depth_texture, 0);
    }
    GLuint stencil_buffer = 0;
    if (g.drawable_stencil_bits > 0) {
        glGenRenderbuffers(1, &stencil_buffer);
        glBindRenderbuffer(GL_RENDERBUFFER, stencil_buffer);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_STENCIL_INDEX8, g.width,
                              g.height);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_STENCIL_ATTACHMENT,
                                  GL_RENDERBUFFER, stencil_buffer);
    }
    int actual_samples = 0, actual_sample_buffers = 0, actual_depth = 0,
        actual_stencil = 0, actual_rgba[4] = {0, 0, 0, 0};
    if (query_target_properties(fbo, &actual_samples, &actual_sample_buffers,
                                &actual_depth, &actual_stencil,
                                actual_rgba) != 0) {
        add_missing("the original-GL control framebuffer is incomplete");
    }
    if (actual_samples != g.drawable_samples) {
        add_missing("control target sample count does not match the drawable");
    }
    if (actual_sample_buffers != g.drawable_sample_buffers) {
        add_missing("control target sample-buffer count does not match the drawable");
    }
    if (actual_depth != g.drawable_depth_bits) {
        add_missing("control target depth precision does not match the drawable");
    }
    if (actual_stencil != g.drawable_stencil_bits) {
        add_missing("control target stencil precision does not match the drawable");
    }
    if (actual_rgba[0] != g.drawable_red_bits ||
        actual_rgba[1] != g.drawable_green_bits ||
        actual_rgba[2] != g.drawable_blue_bits ||
        actual_rgba[3] != g.drawable_alpha_bits) {
        add_missing("control target colour precision does not match the drawable");
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    /* Seed the authorised pre-draw colour state. */
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glViewport(0, 0, g.width, g.height);
    if (g.color_seed_is_file) {
        long size = 0;
        unsigned char *seed = read_file(g.color_seed, &size);
        if (size != (long)g.width * g.height * 4L) {
            free(seed);
            die("colour seed size mismatch", g.color_seed);
        }
        glBindTexture(GL_TEXTURE_2D, color_texture);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, g.width, g.height, GL_BGRA,
                        GL_UNSIGNED_BYTE, seed);
        free(seed);
    } else {
        glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
        glClear(GL_COLOR_BUFFER_BIT);
    }
    if (g.drawable_depth_bits > 0) {
        if (g.depth_seed_is_file) {
            long size = 0;
            unsigned char *seed = read_file(g.depth_seed, &size);
            if (size != (long)g.width * g.height * (long)sizeof(float)) {
                free(seed);
                die("depth seed size mismatch", g.depth_seed);
            }
            glBindTexture(GL_TEXTURE_2D, depth_texture);
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, g.width, g.height,
                            GL_DEPTH_COMPONENT, GL_FLOAT, seed);
            free(seed);
        } else {
            glClearDepth(g.depth_clear);
            glClear(GL_DEPTH_BUFFER_BIT);
        }
    }
    if (g.drawable_stencil_bits > 0) {
        if (g.stencil_seed_is_file) {
            long size = 0;
            unsigned char *seed = read_file(g.stencil_seed, &size);
            int uniform = all_stencil_equal(seed, size);
            if (uniform >= 0) {
                glClearStencil(uniform);
                glClear(GL_STENCIL_BUFFER_BIT);
            } else if (!g.stencil_test) {
                glClearStencil(0);
                glClear(GL_STENCIL_BUFFER_BIT);
            } else {
                snprintf(g.missing[0], sizeof(g.missing[0]),
                         "a non-uniform stencil seed cannot be uploaded exactly "
                         "through the original-GL stencil path");
                g.missing_count = 1;
                g.unsupported = 1;
            }
            free(seed);
        } else {
            glClearStencil(g.stencil_clear);
            glClear(GL_STENCIL_BUFFER_BIT);
        }
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    for (int i = 0; i < g.texture_count; i++) {
        texture_unit *unit = &g.textures[i];
        if (unit->target != GL_TEXTURE_2D) {
            add_missing("only GL_TEXTURE_2D sampler targets are supported by "
                        "the selected-draw control");
            continue;
        }
        glActiveTexture(GL_TEXTURE0 + unit->unit);
        GLuint texture = 0;
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        for (int j = 0; j < g.level_count; j++) {
            texture_level *level = &g.levels[j];
            if (level->unit != unit->unit) continue;
            long size = 0;
            unsigned char *pixels = read_file(level->path, &size);
            if (size != level->bytes) {
                free(pixels);
                die("texture level size mismatch", level->path);
            }
            if (level->compressed) {
                glCompressedTexImage2D(GL_TEXTURE_2D, level->level,
                                       (GLenum)level->internal_format,
                                       level->width, level->height, 0,
                                       (GLsizei)size, pixels);
            } else {
                /* The recorder always reads uncompressed levels as RGBA8 or
                   RGBA float, whatever the internal format.  The upload
                   layout is therefore RGBA, never inferred from the internal
                   format (RGB8/RGBA16 would otherwise be misread). */
                int float_level = level->internal_format == GL_RGBA16F ||
                                  level->internal_format == GL_RGBA32F ||
                                  level->internal_format == GL_RGB16F ||
                                  level->internal_format == GL_RGB32F ||
                                  level->internal_format == GL_RGBA16 ||
                                  level->internal_format == GL_RGB16;
                glTexImage2D(GL_TEXTURE_2D, level->level,
                             (GLint)level->internal_format, level->width,
                             level->height, 0, GL_RGBA,
                             float_level ? GL_FLOAT : GL_UNSIGNED_BYTE,
                             pixels);
            }
            free(pixels);
        }
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, unit->base_level);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, unit->max_level);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, (GLint)unit->min_filter);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, (GLint)unit->mag_filter);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, (GLint)unit->wrap_s);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, (GLint)unit->wrap_t);
        glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MIN_LOD, unit->min_lod);
        glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_LOD, unit->max_lod);
        glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_LOD_BIAS, unit->lod_bias);
        if (unit->compare_mode != 0) {
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_COMPARE_MODE,
                            (GLint)unit->compare_mode);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_COMPARE_FUNC,
                            (GLint)unit->compare_func);
        }
        if (unit->anisotropy > 1) {
            glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT,
                            (float)unit->anisotropy);
        }
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    GLuint program = glCreateProgram();
    GLuint vertex = compile_shader(GL_VERTEX_SHADER, g.vertex_shader);
    GLuint pixel = compile_shader(GL_FRAGMENT_SHADER, g.pixel_shader);
    if (vertex == 0 || pixel == 0) {
        char path[CTL_PATH];
        snprintf(path, sizeof(path), "%s/execution.json", out_dir);
        char body[512];
        snprintf(body, sizeof(body),
                 "{\"schema\":\"gfx-gl-control-execution/v1\","
                 "\"execution\":\"failed\",\"reason\":\"%s\"}", g.failed);
        write_file(path, body, strlen(body));
        return 3;
    }
    glAttachShader(program, vertex);
    glAttachShader(program, pixel);
    for (int i = 0; i < g.attribute_count; i++) {
        glBindAttribLocation(program, (GLuint)g.attributes[i].index,
                             g.attributes[i].name);
    }
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        char log[8192];
        GLsizei length = 0;
        glGetProgramInfoLog(program, sizeof(log), &length, log);
        fprintf(stderr, "draw_control: link failed:\n%.*s\n", (int)length, log);
        snprintf(g.failed, sizeof(g.failed), "shader_link_failed");
        char path[CTL_PATH];
        snprintf(path, sizeof(path), "%s/execution.json", out_dir);
        char body[512];
        snprintf(body, sizeof(body),
                 "{\"schema\":\"gfx-gl-control-execution/v1\","
                 "\"execution\":\"failed\",\"reason\":\"%s\"}", g.failed);
        write_file(path, body, strlen(body));
        return 3;
    }
    glUseProgram(program);

    for (int i = 0; i < g.uniform_count; i++) {
        uniform_record *item = &g.uniforms[i];
        char query[CTL_NAME];
        snprintf(query, sizeof(query), "%s", item->name);
        GLint location = glGetUniformLocation(program, query);
        if (location < 0 && strstr(item->name, "[0]") != NULL) {
            snprintf(query, sizeof(query), "%.*s",
                     (int)(strstr(item->name, "[0]") - item->name), item->name);
            location = glGetUniformLocation(program, query);
        }
        if (location < 0) {
            add_missing("a captured uniform is not active in the linked program");
            continue;
        }
        long size = 0;
        unsigned char *data = read_file(item->path, &size);
        if (!uniform_float_components_ok(item->type, item->components,
                                         item->elements, size)) {
            free(data);
            add_missing("only float1/2/3/4 array uniforms are supported");
            continue;
        }
        if (item->components == 1) {
            glUniform1fv(location, item->elements, (const GLfloat *)data);
        } else if (item->components == 2) {
            glUniform2fv(location, item->elements, (const GLfloat *)data);
        } else if (item->components == 3) {
            glUniform3fv(location, item->elements, (const GLfloat *)data);
        } else {
            glUniform4fv(location, item->elements, (const GLfloat *)data);
        }
        free(data);
    }
    if (g.sampler_count > 0) {
        for (int i = 0; i < g.sampler_count; i++) {
            sampler_binding *item = &g.samplers[i];
            int unit_captured = 0;
            for (int j = 0; j < g.texture_count; j++) {
                if (g.textures[j].unit == item->unit) unit_captured = 1;
            }
            if (!unit_captured) {
                add_missing("a captured sampler uniform names an untracked texture unit");
                continue;
            }
            GLint location = glGetUniformLocation(program, item->name);
            if (location < 0) {
                add_missing("a captured sampler uniform is not active");
                continue;
            }
            glUniform1i(location, item->unit);
        }
    } else {
        /* Legacy control files carry the sampler name on the texture record.
           New packages always emit one explicit sampler line per active
           uniform, so deduplicated texture uploads cannot drop a binding. */
        for (int i = 0; i < g.texture_count; i++) {
            GLint location = glGetUniformLocation(program, g.textures[i].sampler);
            if (location < 0) {
                add_missing("a captured sampler uniform is not active");
                continue;
            }
            glUniform1i(location, g.textures[i].unit);
        }
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    if (g.blend_enabled) {
        const unsigned blend_factors[4] = {
            g.blend_src_rgb, g.blend_dst_rgb, g.blend_src_alpha,
            g.blend_dst_alpha};
        for (int i = 0; i < 4; i++) {
            if (blend_factors[i] == 0x8001 || blend_factors[i] == 0x8002 ||
                blend_factors[i] == 0x8003 || blend_factors[i] == 0x8004) {
                add_missing(
                    "a constant blend colour is not captured; the renderer "
                    "cannot reproduce this blend state");
                break;
            }
        }
        if (g.unsupported) {
            write_unsupported(out_dir);
            return 0;
        }
        glEnable(GL_BLEND);
        glBlendFuncSeparate((GLenum)g.blend_src_rgb, (GLenum)g.blend_dst_rgb,
                            (GLenum)g.blend_src_alpha,
                            (GLenum)g.blend_dst_alpha);
        glBlendEquationSeparate((GLenum)g.blend_eq_rgb,
                                (GLenum)g.blend_eq_alpha);
    } else {
        glDisable(GL_BLEND);
    }
    glColorMask((GLboolean)g.color_mask[0], (GLboolean)g.color_mask[1],
                (GLboolean)g.color_mask[2], (GLboolean)g.color_mask[3]);
    if (g.depth_test) glEnable(GL_DEPTH_TEST); else glDisable(GL_DEPTH_TEST);
    glDepthFunc((GLenum)g.depth_func);
    glDepthMask(g.depth_write ? GL_TRUE : GL_FALSE);
    glDepthRange(g.depth_range[0], g.depth_range[1]);
    if (g.stencil_test) glEnable(GL_STENCIL_TEST); else glDisable(GL_STENCIL_TEST);
    glStencilFunc((GLenum)g.stencil_func, g.stencil_ref,
                  (GLuint)g.stencil_value_mask);
    glStencilOp((GLenum)g.stencil_fail, (GLenum)g.stencil_zfail,
                (GLenum)g.stencil_zpass);
    glStencilMask((GLuint)g.stencil_write_mask);
    if (g.cull_enabled) {
        glEnable(GL_CULL_FACE);
        glCullFace((GLenum)g.cull_mode);
        glFrontFace((GLenum)g.front_face);
    } else {
        glDisable(GL_CULL_FACE);
    }
    if (!g.polygon_mode_seen) {
        add_missing("the control file has no captured front/back polygon mode");
    } else if (g.polygon_mode[0] < GL_POINT || g.polygon_mode[0] > GL_FILL ||
        g.polygon_mode[1] < GL_POINT || g.polygon_mode[1] > GL_FILL) {
        add_missing("an unsupported front/back polygon mode was captured");
    } else {
        /* The captured front and back modes are distinct state.  Applying one
           value to GL_FRONT_AND_BACK would silently fill back-facing wireframe
           geometry. */
        glPolygonMode(GL_FRONT, (GLenum)g.polygon_mode[0]);
        glPolygonMode(GL_BACK, (GLenum)g.polygon_mode[1]);
    }
    if (g.polygon_offset_fill) {
        glEnable(GL_POLYGON_OFFSET_FILL);
        glPolygonOffset(g.polygon_offset_factor, g.polygon_offset_units);
    } else {
        glDisable(GL_POLYGON_OFFSET_FILL);
    }
    if (g.alpha_test) {
        glEnable(GL_ALPHA_TEST);
        glAlphaFunc((GLenum)g.alpha_func, g.alpha_ref);
    } else {
        glDisable(GL_ALPHA_TEST);
    }
    if (g.scissor_test) {
        glEnable(GL_SCISSOR_TEST);
        glScissor(g.scissor_box[0], g.scissor_box[1], g.scissor_box[2],
                  g.scissor_box[3]);
    } else {
        glDisable(GL_SCISSOR_TEST);
    }
    glViewport(g.viewport[0], g.viewport[1], g.viewport[2], g.viewport[3]);
    if (g.state_dither) glEnable(GL_DITHER); else glDisable(GL_DITHER);
    if (g.state_multisample) glEnable(GL_MULTISAMPLE);
    else glDisable(GL_MULTISAMPLE);
    if (g.state_logic_op_enabled) {
        glEnable(GL_COLOR_LOGIC_OP);
        glLogicOp((GLenum)g.state_logic_op);
    } else {
        glDisable(GL_COLOR_LOGIC_OP);
    }
    if (g.state_srgb) glEnable(GL_FRAMEBUFFER_SRGB);
    else glDisable(GL_FRAMEBUFFER_SRGB);

    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    GLuint attribute_buffers[MAX_ATTRIBUTES];
    memset(attribute_buffers, 0, sizeof(attribute_buffers));
    for (int i = 0; i < g.attribute_count; i++) {
        attribute_record *item = &g.attributes[i];
        long size = 0;
        unsigned char *data = read_file(item->path, &size);
        GLuint buffer = 0;
        glGenBuffers(1, &buffer);
        glBindBuffer(GL_ARRAY_BUFFER, buffer);
        glBufferData(GL_ARRAY_BUFFER, size, data, GL_STATIC_DRAW);
        attribute_buffers[i] = buffer;
        free(data);
        if (!item->enabled) continue;
        if (item->integer) {
            add_missing("integer vertex attributes are not supported by the "
                        "selected-draw control");
            continue;
        }
        if (item->divisor != 0) {
            add_missing("instanced attribute divisors are not supported by the "
                        "selected-draw control");
            continue;
        }
        if (item->offset < 0 || item->offset > size) {
            add_missing("attribute offset is outside its captured range");
            continue;
        }
        glEnableVertexAttribArray((GLuint)item->index);
        glVertexAttribPointer((GLuint)item->index, item->components,
                              (GLenum)item->type,
                              (GLboolean)(item->normalized ? GL_TRUE : GL_FALSE),
                              item->stride, (const void *)(intptr_t)item->offset);
    }
    if (g.unsupported) {
        write_unsupported(out_dir);
        return 0;
    }

    GLuint index_buffer = 0;
    unsigned index_type = 0;
    long index_size = 0;
    if (g.indexed) {
        if (g.first_index != 0 || g.base_vertex != 0) {
            add_missing("a non-zero first index or base vertex is not supported "
                        "by the selected-draw control");
        }
        if (g.instance_count != 1) {
            add_missing("instanced draws are not supported by the selected-draw "
                        "control");
        }
        if (g.index_path[0] == 0) {
            add_missing("the selected indexed draw has no captured index data");
        }
        if (g.unsupported) {
            write_unsupported(out_dir);
            return 0;
        }
        unsigned char *indices = read_file(g.index_path, &index_size);
        if (g.count > 0 && index_size % g.count == 0) {
            long per_index = index_size / g.count;
            if (per_index == 1) index_type = GL_UNSIGNED_BYTE;
            else if (per_index == 2) index_type = GL_UNSIGNED_SHORT;
            else if (per_index == 4) index_type = GL_UNSIGNED_INT;
        }
        if (index_type == 0) {
            free(indices);
            add_missing("the captured index width cannot be derived");
            write_unsupported(out_dir);
            return 0;
        }
        glGenBuffers(1, &index_buffer);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, index_buffer);
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, index_size, indices, GL_STATIC_DRAW);
        free(indices);
    }

    int draw_calls = 1;
    if (g.indexed) {
        glDrawElements((GLenum)g.mode, g.count, (GLenum)index_type,
                       (const void *)0);
    } else {
        glDrawArrays((GLenum)g.mode, g.first, g.count);
    }
    glFinish();
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        char path[CTL_PATH];
        snprintf(path, sizeof(path), "%s/execution.json", out_dir);
        char body[512];
        snprintf(body, sizeof(body),
                 "{\"schema\":\"gfx-gl-control-execution/v1\","
                 "\"execution\":\"failed\",\"reason\":\"gl_error\","
                 "\"gl_error\":%u}", (unsigned)error);
        write_file(path, body, strlen(body));
        return 4;
    }

    unsigned char *result = xmalloc((size_t)g.width * (size_t)g.height * 4u);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glReadPixels(0, 0, g.width, g.height, GL_BGRA, GL_UNSIGNED_BYTE, result);
    error = glGetError();
    if (error != GL_NO_ERROR) {
        free(result);
        char path[CTL_PATH];
        snprintf(path, sizeof(path), "%s/execution.json", out_dir);
        char body[512];
        snprintf(body, sizeof(body),
                 "{\"schema\":\"gfx-gl-control-execution/v1\","
                 "\"execution\":\"failed\",\"reason\":\"readback_error\","
                 "\"gl_error\":%u}", (unsigned)error);
        write_file(path, body, strlen(body));
        return 4;
    }
    write_file(g.out, result, (size_t)g.width * (size_t)g.height * 4u);
    free(result);

    /* Optional post-draw depth/stencil readback for the preflight fixtures.
       These files are diagnostics of the one executed draw; the qualified
       comparison remains the colour image. */
    char depth_after[CTL_PATH] = "";
    char stencil_after[CTL_PATH] = "";
    long depth_after_bytes = 0;
    long stencil_after_bytes = 0;
    if (g.drawable_depth_bits > 0) {
        size_t bytes = (size_t)g.width * (size_t)g.height * sizeof(float);
        float *values = xmalloc(bytes);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glReadPixels(0, 0, g.width, g.height, GL_DEPTH_COMPONENT, GL_FLOAT,
                     values);
        snprintf(depth_after, sizeof(depth_after), "%s/depth-after.f32", out_dir);
        write_file(depth_after, values, bytes);
        depth_after_bytes = (long)bytes;
        free(values);
    }
    if (g.drawable_stencil_bits > 0) {
        size_t bytes = (size_t)g.width * (size_t)g.height;
        unsigned char *values = xmalloc(bytes);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glReadPixels(0, 0, g.width, g.height, GL_STENCIL_INDEX,
                     GL_UNSIGNED_BYTE, values);
        snprintf(stencil_after, sizeof(stencil_after), "%s/stencil-after.u8",
                 out_dir);
        write_file(stencil_after, values, bytes);
        stencil_after_bytes = (long)bytes;
        free(values);
    }
    error = glGetError();
    if (error != GL_NO_ERROR) {
        char failure[CTL_PATH];
        snprintf(failure, sizeof(failure), "%s/execution.json", out_dir);
        char body[512];
        snprintf(body, sizeof(body),
                 "{\"schema\":\"gfx-gl-control-execution/v1\","
                 "\"execution\":\"failed\",\"reason\":\"aux_readback_error\","
                 "\"gl_error\":%u}", (unsigned)error);
        write_file(failure, body, strlen(body));
        return 4;
    }

    char path[CTL_PATH];
    snprintf(path, sizeof(path), "%s/execution.json", out_dir);
    char vendor_json[1024], renderer_json[1024], version_json[1024];
    json_escape(vendor, vendor_json, sizeof(vendor_json));
    json_escape(renderer, renderer_json, sizeof(renderer_json));
    json_escape(version, version_json, sizeof(version_json));
    char buffer[8192];
    snprintf(buffer, sizeof(buffer),
             "{\"schema\":\"gfx-gl-control-execution/v1\","
             "\"execution\":\"completed\","
             "\"gl\":{\"vendor\":\"%s\",\"renderer\":\"%s\","
             "\"version\":\"%s\",\"draw_calls\":%d,\"error\":0,"
             "\"target\":{\"samples\":%d,\"sample_buffers\":%d,"
             "\"depth_bits\":%d,\"stencil_bits\":%d,"
             "\"red_bits\":%d,\"green_bits\":%d,\"blue_bits\":%d,"
             "\"alpha_bits\":%d,\"width\":%d,\"height\":%d,"
             "\"framebuffer_srgb\":%s}},"
             "\"artifact\":{\"file\":\"result.bgra\",\"bytes\":%ld,"
             "\"width\":%d,\"height\":%d,\"pixel_format\":\"BGRA8\","
             "\"origin\":\"lower_left\"},"
             "\"depth_after\":{\"file\":\"depth-after.f32\",\"bytes\":%ld},"
             "\"stencil_after\":{\"file\":\"stencil-after.u8\",\"bytes\":%ld}}",
             vendor_json, renderer_json, version_json, draw_calls,
             actual_samples,
             actual_sample_buffers, actual_depth, actual_stencil, actual_rgba[0],
             actual_rgba[1], actual_rgba[2], actual_rgba[3], g.width, g.height,
             g.state_srgb ? "true" : "false",
             (long)g.width * (long)g.height * 4L, g.width, g.height,
             depth_after_bytes, stencil_after_bytes);
    write_file(path, buffer, strlen(buffer));
    return 0;
}
