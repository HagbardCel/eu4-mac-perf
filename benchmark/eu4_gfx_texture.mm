#import "eu4_gfx_texture.h"

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <limits.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>

/* Bytes per pixel for formats 7..23, from the table at RVA 0x1a793a0. */
static const uint8_t kBytes[17] = {2, 2, 3, 3, 4, 4, 4, 4, 1, 1, 2, 4, 8, 4, 8, 12, 16};

typedef struct {
    uint32_t width;
    uint32_t height;
    uint32_t mips;
    uint32_t format;
    uint32_t kind;
} tex_desc;

typedef struct {
    uint32_t level;
    uint32_t face;
    uint32_t width;
    uint32_t height;
    uint32_t bytes;
    uint32_t pad;
    const void *pixels;
} tex_image;

_Static_assert(sizeof(tex_image) == 32, "image record");
_Static_assert(offsetof(tex_image, pixels) == 24, "pixel pointer");
_Static_assert(offsetof(tex_image, bytes) == 16, "compressed size");

static void write_u32(uint8_t *header, int offset, uint32_t value) {
    memcpy(header + offset, &value, sizeof value);
}

static int g_no_bc;

void eu4_gfx_test_no_bc(int on) { g_no_bc = on; }

static uint32_t mip_size(uint32_t base, uint32_t level) {
    uint32_t value = base >> level;
    return value == 0 ? 1 : value;
}

/* One level at the base size, then one more while either side is still above 1. */
static uint32_t mip_capacity(uint32_t width, uint32_t height) {
    uint32_t count = 1;
    while (width > 1 || height > 1) {
        width = width > 1 ? width >> 1 : 1;
        height = height > 1 ? height >> 1 : 1;
        count++;
    }
    return count;
}

/* S3TC block size in bytes: DXT1 = 8, DXT3/DXT5 = 16. */
static uint32_t block_bytes_for(uint32_t format) {
    switch (format) {
        case 1: /* DXT1 */
        case 2: /* DXT1 sRGB */
            return 8;
        case 3: /* DXT3 */
        case 4: /* DXT3 sRGB */
        case 5: /* DXT5 */
        case 6: /* DXT5 sRGB */
            return 16;
        default:
            return 0;
    }
}

static int block_span(uint32_t width, uint32_t height, uint32_t block, uint32_t *span) {
    uint32_t blocks_wide = (width + 3) / 4;
    uint32_t blocks_high = (height + 3) / 4;
    if (blocks_wide == 0 || blocks_high > UINT32_MAX / blocks_wide) {
        return -1;
    }
    uint32_t blocks = blocks_wide * blocks_high;
    if (block == 0 || blocks > UINT32_MAX / block) {
        return -1;
    }
    uint32_t bytes = blocks * block;
    if (bytes > (uint32_t)INT_MAX) {
        return -1;
    }
    *span = bytes;
    return 0;
}

static void free_staged(uint8_t **staged, uint32_t count) {
    if (staged == NULL) {
        return;
    }
    for (uint32_t i = 0; i < count; i++) {
        free(staged[i]);
        staged[i] = NULL;
    }
}

/* Uncompressed rows are tightly packed. GL_UNPACK_ALIGNMENT (0xCF5) is set to 1 at
   RVA 0x15ec561 and restored to 4 at 0x15ec5c1, around glTexImage2D at 0x15ec5b8. */
static int source_span(uint32_t width, uint32_t height, uint32_t bpp, uint32_t *span) {
    if (bpp == 0 || width > UINT32_MAX / bpp) {
        return -1;
    }
    uint32_t row = width * bpp;
    if (row == 0 || height > UINT32_MAX / row) {
        return -1;
    }
    uint32_t bytes = row * height;
    if (bytes > (uint32_t)INT_MAX) {
        return -1;
    }
    *span = bytes;
    return 0;
}

static int stage_bytes(const void *pixels, uint32_t span, uint8_t **kept) {
    uint8_t *copy = (uint8_t *)malloc(span);
    if (copy == NULL) {
        return EU4_GFX_TEX_PIXELS;
    }
    int got = 0;
    if (eu4_metal_events_read(pixels, copy, (int)span, &got) != 0 || got != (int)span) {
        free(copy);
        return EU4_GFX_TEX_PIXELS;
    }
    if (kept == NULL) {
        free(copy);
    } else {
        *kept = copy;
    }
    return EU4_GFX_TEX_OK;
}

static int stage_record(const tex_image *record, uint32_t bpp, uint8_t **kept) {
    uint32_t span = 0;
    if (source_span(record->width, record->height, bpp, &span) != 0) {
        return EU4_GFX_TEX_SIZE;
    }
    return stage_bytes(record->pixels, span, kept);
}

static int validate(void *header, const void *desc, const void *images, uint32_t count, uint8_t **staged) {
    if (header == NULL || desc == NULL || (count > 0 && images == NULL)) {
        return EU4_GFX_TEX_HEADER;
    }
    tex_desc info;
    memcpy(&info, desc, sizeof info);
    int cube = info.kind == 1;
    if (!cube && info.kind != 0) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (cube && info.width != info.height) {
        return EU4_GFX_TEX_CUBEMAP;
    }
    uint32_t block = block_bytes_for(info.format);
    int compressed = block != 0;
    int bgra = info.format == 0xb || info.format == 0xd;
    int srgb = info.format == 0xc || info.format == 0xe;
    int bgr = info.format == 9;
    int luminance = info.format == 16; /* GL_LUMINANCE8 */
    int alpha_only = info.format == 15; /* GL_ALPHA8 */
    int floating = info.format >= 17 && info.format <= 23;
    if (!compressed && !bgra && !srgb && !bgr && !luminance && !alpha_only &&
        !floating) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (!compressed && (info.format < 7 || info.format > 23)) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (info.width == 0 || info.height == 0 || info.width > 16384 || info.height > 16384) {
        return EU4_GFX_TEX_SIZE;
    }
    if (count > (cube ? 96u : 16u)) {
        return EU4_GFX_TEX_LEVEL;
    }
    uint32_t raw_mips = info.mips;
    uint32_t mips = raw_mips == 0 ? 1 : raw_mips;
    if (compressed && mips > mip_capacity(info.width, info.height)) {
        return EU4_GFX_TEX_LEVEL;
    }
    const tex_image *records = (const tex_image *)images;
    for (uint32_t i = 0; i < count; i++) {
        if (records[i].level >= 16) {
            return EU4_GFX_TEX_LEVEL;
        }
        if (compressed && !cube) {
            if (records[i].level >= mips) {
                return EU4_GFX_TEX_LEVEL;
            }
        } else if (records[i].level + 1 > mips) {
            mips = records[i].level + 1;
        }
    }
    if (!compressed && mips > 16) {
        return EU4_GFX_TEX_LEVEL;
    }
    uint32_t bpp = compressed ? 1 : kBytes[info.format - 7];
    for (uint32_t i = 0; i < count; i++) {
        uint32_t expect_w = mip_size(info.width, records[i].level);
        uint32_t expect_h = mip_size(info.height, records[i].level);
        if (records[i].face > (cube ? 5u : 0u)) {
            free_staged(staged, i);
            return EU4_GFX_TEX_FORMAT;
        }
        if (records[i].width != expect_w || records[i].height != expect_h || records[i].pixels == NULL) {
            free_staged(staged, i);
            return records[i].pixels == NULL ? EU4_GFX_TEX_PIXELS : EU4_GFX_TEX_SIZE;
        }
        uint8_t *copy = NULL;
        int staged_status;
        if (compressed) {
            uint32_t span = 0;
            if (block_span(records[i].width, records[i].height, block, &span) != 0 ||
                records[i].bytes != span) {
                free_staged(staged, i);
                return EU4_GFX_TEX_SIZE;
            }
            staged_status = stage_bytes(records[i].pixels, span, staged == NULL ? NULL : &copy);
        } else {
            staged_status = stage_record(&records[i], bpp, staged == NULL ? NULL : &copy);
        }
        if (staged_status != EU4_GFX_TEX_OK) {
            free_staged(staged, i);
            return staged_status;
        }
        if (staged != NULL) {
            staged[i] = copy;
        }
    }
    uint8_t *bytes = (uint8_t *)header;
    write_u32(bytes, 4, 0);
    bytes[0x18] = 0;
    bytes[0x19] = 0;
    bytes[0x1a] = 0;
    if (compressed) {
        /* The engine writes only the S3TC internal format; +0x20/+0x24 stay put. */
        static const uint32_t compressed_internal[7] = {
            0, 0x83f1, 0x8c4d, 0x83f2, 0x8c4e, 0x83f3, 0x8c4f};
        write_u32(bytes, 0x1c, compressed_internal[info.format]);
    } else {
        uint32_t internal = bgra ? 0x8058 : 0x8c43;
        uint32_t gl_format = 0x80e1;
        if (bgr) {
            internal = 0x8051;
            gl_format = 0x80e0;
        }
        write_u32(bytes, 0x1c, internal);
        write_u32(bytes, 0x20, gl_format);
        write_u32(bytes, 0x24, 0x1401);
    }
    write_u32(bytes, 0x28, info.width);
    write_u32(bytes, 0x2c, info.height);
    write_u32(bytes, 0x30, info.format);
    write_u32(bytes, 0x34, bpp);
    write_u32(bytes, 0x38, info.width);
    write_u32(bytes, 0x3c, info.height);
    write_u32(bytes, 0x40, compressed ? raw_mips : mips);
    return EU4_GFX_TEX_OK;
}

int eu4_gfx_texture_prepare(void *header, const void *desc, const void *images, uint32_t count) {
    return validate(header, desc, images, count, NULL);
}

static uint8_t *expand_bgr(const uint8_t *bgr, uint32_t width, uint32_t height) {
    if (width > UINT32_MAX / 4 || height > UINT32_MAX / (width * 4)) {
        return NULL;
    }
    uint32_t pixels = width * height;
    uint8_t *rgba = (uint8_t *)malloc((size_t)pixels * 4);
    if (rgba == NULL) {
        return NULL;
    }
    for (uint32_t i = 0; i < pixels; i++) {
        rgba[i * 4] = bgr[i * 3 + 2];
        rgba[i * 4 + 1] = bgr[i * 3 + 1];
        rgba[i * 4 + 2] = bgr[i * 3];
        rgba[i * 4 + 3] = 0xff;
    }
    return rgba;
}

int eu4_gfx_texture_make(void *device, void *header, const void *desc, const void *images, uint32_t count,
                         void **out_texture) {
    if (out_texture != NULL) {
        *out_texture = NULL;
    }
    /* A cube may carry up to 96 face/level records (6 faces x 16 levels);
       the staging pointer table must cover every record the validator keeps. */
    uint8_t *staged[128];
    memset(staged, 0, sizeof staged);
    int status = validate(header, desc, images, count, staged);
    if (status != 0) {
        return status;
    }
    if (device == NULL || out_texture == NULL) {
        free_staged(staged, count);
        return EU4_GFX_TEX_BACKEND;
    }
    uint32_t width = 0, height = 0, mips = 0, format = 0;
    memcpy(&width, (uint8_t *)header + 0x28, 4);
    memcpy(&height, (uint8_t *)header + 0x2c, 4);
    memcpy(&format, (uint8_t *)header + 0x30, 4);
    memcpy(&mips, (uint8_t *)header + 0x40, 4);
    uint32_t block = block_bytes_for(format);
    int compressed = block != 0;
    uint32_t kind = 0;
    memcpy(&kind, (const uint8_t *)desc + 0x10, 4);
    int cube = kind == 1;
    uint32_t level_count = mips;
    if (compressed) {
        uint32_t raw = 0;
        memcpy(&raw, (const uint8_t *)desc + 8, 4);
        level_count = raw == 0 ? 1 : raw;
        if (g_no_bc || ![(__bridge id<MTLDevice>)device supportsBCTextureCompression]) {
            free_staged(staged, count);
            return EU4_GFX_TEX_METAL;
        }
    }
    if (cube) {
        const tex_image *cube_records = (const tex_image *)images;
        uint32_t levels = 1;
        for (uint32_t i = 0; i < count; i++) {
            if (cube_records[i].level + 1 > levels) {
                levels = cube_records[i].level + 1;
            }
        }
        level_count = levels;
    }
    MTLPixelFormat pixel;
    if (compressed) {
        switch (format) {
            case 1: pixel = MTLPixelFormatBC1_RGBA; break;
            case 2: pixel = MTLPixelFormatBC1_RGBA_sRGB; break;
            case 3: pixel = MTLPixelFormatBC2_RGBA; break;
            case 4: pixel = MTLPixelFormatBC2_RGBA_sRGB; break;
            case 5: pixel = MTLPixelFormatBC3_RGBA; break;
            case 6: pixel = MTLPixelFormatBC3_RGBA_sRGB; break;
            default: pixel = MTLPixelFormatBC3_RGBA; break;
        }
    } else if (format == 9) {
        pixel = MTLPixelFormatRGBA8Unorm;
    } else if (format == 16 || format == 15) {
        pixel = MTLPixelFormatBGRA8Unorm;
    } else if (format == 0xc || format == 0xe) {
        pixel = MTLPixelFormatBGRA8Unorm_sRGB;
    } else if (format == 17) {
        pixel = MTLPixelFormatR16Float;
    } else if (format == 18) {
        pixel = MTLPixelFormatRG16Float;
    } else if (format == 19) {
        pixel = MTLPixelFormatRGBA16Float;
    } else if (format == 20) {
        pixel = MTLPixelFormatR32Float;
    } else if (format == 21) {
        pixel = MTLPixelFormatRG32Float;
    } else if (format == 22 || format == 23) {
        pixel = MTLPixelFormatRGBA32Float;
    } else {
        pixel = MTLPixelFormatBGRA8Unorm;
    }
    MTLTextureDescriptor *metal = cube
        ? [MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:pixel
                                                                size:width
                                                           mipmapped:level_count > 1]
        : [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
                                                             width:width
                                                            height:height
                                                         mipmapped:level_count > 1];
    metal.mipmapLevelCount = level_count;
    metal.usage = MTLTextureUsageShaderRead;
    metal.storageMode = MTLStorageModeShared;
    id<MTLTexture> texture = [(__bridge id<MTLDevice>)device newTextureWithDescriptor:metal];
    if (texture == nil) {
        free_staged(staged, count);
        return EU4_GFX_TEX_METAL;
    }
    const tex_image *records = (const tex_image *)images;
    for (uint32_t i = 0; i < count; i++) {
        const uint8_t *upload = staged[i];
        uint8_t *expanded = NULL;
        NSUInteger row;
        if (compressed) {
            row = (NSUInteger)((records[i].width + 3) / 4) * block;
        } else if (format == 9 || format == 15 || format == 16) {
            row = (NSUInteger)records[i].width * 4; /* expanded to BGRA8 */
        } else if (format == 22) {
            row = (NSUInteger)records[i].width * 16; /* expanded to RGBA32F */
        } else {
            row = (NSUInteger)records[i].width * kBytes[format - 7];
        }
        if (format == 9) {
            expanded = expand_bgr(staged[i], records[i].width, records[i].height);
            if (expanded == NULL) {
                free_staged(staged, count);
                return EU4_GFX_TEX_PIXELS;
            }
            upload = expanded;
        } else if (format == 22) {
            /* Metal has no RGB32Float; expand to RGBA32Float with alpha 1. */
            size_t pixels = (size_t)records[i].width * records[i].height;
            expanded = (uint8_t *)malloc(pixels * 16);
            if (expanded == NULL) {
                free_staged(staged, count);
                return EU4_GFX_TEX_PIXELS;
            }
            for (size_t p = 0; p < pixels; p++) {
                memcpy(expanded + p * 16, staged[i] + p * 12, 12);
                float one = 1.0f;
                memcpy(expanded + p * 16 + 12, &one, 4);
            }
            upload = expanded;
            row = (NSUInteger)records[i].width * 16;
        } else if (format == 16 || format == 15) {
            /* GL LUMINANCE samples (l,l,l,1); GL ALPHA samples (0,0,0,a).
               Metal has no luminance/alpha-only colour format, so expand to
               BGRA8 with exactly those component values before upload. */
            size_t pixels = (size_t)records[i].width * records[i].height;
            expanded = (uint8_t *)malloc(pixels * 4);
            if (expanded == NULL) {
                free_staged(staged, count);
                return EU4_GFX_TEX_PIXELS;
            }
            for (size_t p = 0; p < pixels; p++) {
                uint8_t value = staged[i][p];
                if (format == 16) {
                    expanded[p * 4 + 0] = value;
                    expanded[p * 4 + 1] = value;
                    expanded[p * 4 + 2] = value;
                    expanded[p * 4 + 3] = 255;
                } else {
                    expanded[p * 4 + 0] = 0;
                    expanded[p * 4 + 1] = 0;
                    expanded[p * 4 + 2] = 0;
                    expanded[p * 4 + 3] = value;
                }
            }
            upload = expanded;
            row = (NSUInteger)records[i].width * 4;
        }
        if (cube) {
            [texture replaceRegion:MTLRegionMake2D(0, 0, records[i].width, records[i].height)
                       mipmapLevel:records[i].level
                             slice:records[i].face
                         withBytes:upload
                       bytesPerRow:row
                     bytesPerImage:row * records[i].height];
        } else {
            [texture replaceRegion:MTLRegionMake2D(0, 0, records[i].width, records[i].height)
                       mipmapLevel:records[i].level
                         withBytes:upload
                       bytesPerRow:row];
        }
        free(expanded);
    }
    free_staged(staged, count);
    *out_texture = (__bridge_retained void *)texture;
    return EU4_GFX_TEX_OK;
}

static const char *branch_name(int status) {
    switch (status) {
    case EU4_GFX_TEX_HEADER:
        return "header";
    case EU4_GFX_TEX_CUBEMAP:
        return "cubemap";
    case EU4_GFX_TEX_COMPRESSED:
        return "compressed";
    case EU4_GFX_TEX_FORMAT:
        return "format";
    case EU4_GFX_TEX_SIZE:
        return "size";
    case EU4_GFX_TEX_PIXELS:
        return "pixels";
    case EU4_GFX_TEX_LEVEL:
        return "level";
    case EU4_GFX_TEX_METAL:
        return "metal";
    default:
        return "backend";
    }
}

static uint8_t *expand_bgr(const uint8_t *bgr, uint32_t width, uint32_t height);

void eu4_gfx_update_texture(void *ignored, const void *pixels, uint32_t width,
                            uint32_t height, uint32_t flags, void *header) {
    (void)ignored;
    (void)flags;
    if (header == NULL) {
        return;
    }
    if (pixels == NULL || width == 0 || height == 0) {
        return;
    }
    id<MTLTexture> texture = (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header);
    if (texture == nil) {
        return;
    }
    uint32_t format = 0;
    uint32_t bpp = 0;
    memcpy(&format, (uint8_t *)header + 0x30, 4);
    memcpy(&bpp, (uint8_t *)header + 0x34, 4);
    if (bpp == 0 && format >= 7 && format <= 23) {
        bpp = kBytes[format - 7];
    }
    const void *upload = pixels;
    uint8_t *expanded = NULL;
    NSUInteger row = (NSUInteger)width * bpp;
    if (bpp == 3) {
        expanded = expand_bgr((const uint8_t *)pixels, width, height);
        if (expanded == NULL) {
            return;
        }
        upload = expanded;
        row = (NSUInteger)width * 4;
    } else if (bpp == 1) {
        size_t count = (size_t)width * height;
        expanded = (uint8_t *)malloc(count * 4);
        if (expanded == NULL) {
            return;
        }
        for (size_t i = 0; i < count; i++) {
            uint8_t v = ((const uint8_t *)pixels)[i];
            expanded[i * 4 + 0] = v;
            expanded[i * 4 + 1] = v;
            expanded[i * 4 + 2] = v;
            expanded[i * 4 + 3] = 255;
        }
        upload = expanded;
        row = (NSUInteger)width * 4;
    } else if (bpp != 4 && !(format >= 17 && format <= 23)) {
        return;
    }
    [texture replaceRegion:MTLRegionMake2D(0, 0, width, height)
               mipmapLevel:0
                 withBytes:upload
               bytesPerRow:row];
    free(expanded);
}

static void write_texture_trace(const char *event, void *header, const void *desc,
                                uint32_t count, int status) {
    if (getenv("EU4_GFX_TEXTURE_TRACE") == NULL) {
        return;
    }
    uint32_t width = 0, height = 0, mips = 0, format = 0, kind = 0;
    if (desc != NULL) {
        memcpy(&width, (const uint8_t *)desc, 4);
        memcpy(&height, (const uint8_t *)desc + 4, 4);
        memcpy(&mips, (const uint8_t *)desc + 8, 4);
        memcpy(&format, (const uint8_t *)desc + 12, 4);
        memcpy(&kind, (const uint8_t *)desc + 16, 4);
    }
    char body[320];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-texture-trace/v1\",\"event\":\"%s\",\"header\":\"%p\","
        "\"width\":%u,\"height\":%u,\"mips\":%u,\"format\":%u,\"kind\":%u,"
        "\"count\":%u,\"status\":%d,\"pid\":%d}\n",
        event, header, width, height, mips, format, kind, count, status,
        (int)getpid());
    if (length > 0 && (size_t)length < sizeof body) {
        (void)eu4_metal_events_write("texture-trace.json", body);
    }
}

static MTLPixelFormat render_target_format(uint32_t format) {
    switch (format) {
        case 9: return MTLPixelFormatRGBA8Unorm;
        case 0xc:
        case 0xe: return MTLPixelFormatBGRA8Unorm_sRGB;
        case 17: return MTLPixelFormatR16Float;
        case 18: return MTLPixelFormatRG16Float;
        case 19: return MTLPixelFormatRGBA16Float;
        case 20: return MTLPixelFormatR32Float;
        case 21: return MTLPixelFormatRG32Float;
        case 22:
        case 23: return MTLPixelFormatRGBA32Float;
        default: return MTLPixelFormatBGRA8Unorm;
    }
}

static uint32_t render_target_bytes(uint32_t format) {
    if (format >= 7 && format <= 23) {
        return kBytes[format - 7];
    }
    return 4;
}

void eu4_gfx_initialize_render_target_texture(void *handle, void *header,
                                              uint32_t width, uint32_t height,
                                              uint32_t format, uint32_t flags,
                                              uint32_t samples) {
    (void)handle;
    (void)samples;
    if (header == NULL) {
        return;
    }
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (device == nil || backend == nil) {
        return;
    }
    if (width == 0 || height == 0) {
        return;
    }
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:render_target_format(format)
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> color = [device newTextureWithDescriptor:descriptor];
    id<MTLTexture> depth = nil;
    if (color == nil) {
        return;
    }
    if (flags != 0) {
        MTLTextureDescriptor *depth_descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
                                                               width:width
                                                              height:height
                                                           mipmapped:NO];
        depth_descriptor.usage = MTLTextureUsageRenderTarget;
        depth_descriptor.storageMode = MTLStorageModePrivate;
        depth = [device newTextureWithDescriptor:depth_descriptor];
    }
    if (backend.textures == nil) {
        backend.textures = [NSMutableDictionary dictionary];
    }
    if (backend.renderTargetDepths == nil) {
        backend.renderTargetDepths = [NSMutableDictionary dictionary];
    }
    /* The engine may re-initialise the same render-target header every frame.
       Replacing the texture would discard the map that the previous frame's
       resolve produced; reuse the existing storage when the geometry matches. */
    id<MTLTexture> existing = backend.textures[[NSValue valueWithPointer:header]];
    if (existing != nil && existing.width == width && existing.height == height &&
        existing.pixelFormat == render_target_format(format)) {
        color = existing;
        depth = backend.renderTargetDepths[[NSValue valueWithPointer:header]];
    }
    backend.textures[[NSValue valueWithPointer:header]] = color;
    if (depth != nil) {
        backend.renderTargetDepths[[NSValue valueWithPointer:header]] = depth;
    }
    uint8_t *bytes = (uint8_t *)header;
    memset(bytes, 0, 0x44);
    bytes[0x19] = 1;
    bytes[0x1a] = flags != 0 ? 1 : 0;
    bytes[0x1b] = 0;
    uint32_t bpp = render_target_bytes(format);
    memcpy(bytes + 0x28, &width, 4);
    memcpy(bytes + 0x2c, &height, 4);
    memcpy(bytes + 0x30, &format, 4);
    memcpy(bytes + 0x34, &bpp, 4);
    memcpy(bytes + 0x38, &width, 4);
    memcpy(bytes + 0x3c, &height, 4);
    uint32_t zero = 0;
    memcpy(bytes + 0x40, &zero, 4);
}

void *eu4_gfx_create_render_target_texture(void *handle, uint32_t width,
                                           uint32_t height, uint32_t format,
                                           uint32_t flags, uint32_t samples) {
    uint8_t *header = (uint8_t *)calloc(1, 0x44);
    if (header == NULL) {
        return NULL;
    }
    eu4_gfx_initialize_render_target_texture(handle, header, width, height,
                                             format, flags, samples);
    return header;
}

void *eu4_gfx_depth_texture_for_header(void *header) {
    if (header == NULL) {
        return NULL;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.renderTargetDepths == nil) {
        return NULL;
    }
    id depth = backend.renderTargetDepths[[NSValue valueWithPointer:header]];
    return (__bridge void *)depth;
}

void eu4_gfx_initialize_texture(void *ignored, void *header, const void *desc, const void *images,
                                uint32_t count) {
    const void *caller = __builtin_return_address(0);
    (void)ignored;
    eu4_gfx_require_thread("_GfxInitializeTexture", caller);
    write_texture_trace("begin", header, desc, count, -1);
    void *gpu = NULL;
    int status = eu4_gfx_texture_make(eu4_gfx_metal_device(), header, desc, images, count, &gpu);
    write_texture_trace("end", header, desc, count, status);
    if (status != 0) {
        uint32_t format = 0;
        uint32_t kind = 0;
        if (desc != NULL) {
            memcpy(&format, (const uint8_t *)desc + 12, 4);
            memcpy(&kind, (const uint8_t *)desc + 16, 4);
        }
        char image[512];
        uint64_t rva = 0;
        int inside = 0;
        uint64_t thread = 0;
        int pid = 0;
        eu4_metal_events_describe(caller, image, sizeof image, &rva, &inside, &thread, &pid);
        char body[1280];
        int length = snprintf(
            body, sizeof body,
            "{\"symbol\":\"_GfxInitializeTexture\",\"branch\":\"%s\",\"format\":%u,\"kind\":%u,"
            "\"image\":\"%s\",\"caller_rva\":%s,\"thread\":%llu,\"pid\":%d}\n",
            branch_name(status), format, kind, image, inside ? "\"0\"" : "null",
            (unsigned long long)thread, pid);
        if (inside) {
            length = snprintf(body, sizeof body,
                              "{\"symbol\":\"_GfxInitializeTexture\",\"branch\":\"%s\",\"format\":%u,"
                              "\"kind\":%u,\"image\":\"%s\",\"caller_rva\":\"0x%llx\",\"thread\":%llu,"
                              "\"pid\":%d}\n",
                              branch_name(status), format, kind, image, (unsigned long long)rva,
                              (unsigned long long)thread, pid);
        }
        if (length > 0 && (size_t)length < sizeof body) {
            (void)eu4_metal_events_write("blocked_at.json", body);
        }
        if (getenv("EU4_GFX_TEXTURE_PLACEHOLDER") != NULL) {
            /* Diagnostic collection mode only: an unsupported texture becomes a
               1x1 opaque placeholder so a long startup can still be traversed.
               Production and acceptance runs keep fail-closed behaviour. */
            id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
            if (device != nil) {
                MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                              width:1
                                                                                             height:1
                                                                                          mipmapped:NO];
                id<MTLTexture> placeholder = [device newTextureWithDescriptor:d];
                const uint8_t white[4] = {0xff, 0xff, 0xff, 0xff};
                [placeholder replaceRegion:MTLRegionMake2D(0, 0, 1, 1)
                               mipmapLevel:0
                                 withBytes:white
                               bytesPerRow:4];
                EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
                if (backend != nil && placeholder != nil) {
                    if (backend.textures == nil) {
                        backend.textures = [NSMutableDictionary dictionary];
                    }
                    backend.textures[[NSValue valueWithPointer:header]] = placeholder;
                    char note[256];
                    int n = snprintf(note, sizeof note,
                                     "{\"schema\":\"gfx-texture-placeholder/v1\","
                                     "\"format\":%u,\"kind\":%u,\"status\":\"%s\","
                                     "\"pid\":%d}\n",
                                     format, kind, branch_name(status), (int)getpid());
                    if (n > 0 && (size_t)n < sizeof note) {
                        (void)eu4_metal_events_write("texture-placeholder.json", note);
                    }
                    return;
                }
            }
        }
        eu4_gfx_stop("_GfxInitializeTexture", "blocked_at", caller);
    }
    id<MTLTexture> taken = (__bridge_transfer id<MTLTexture>)gpu;
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil) {
        taken = nil;
        eu4_gfx_stop("_GfxInitializeTexture", "blocked_at", caller);
    }
    if (backend.textures == nil) {
        backend.textures = [NSMutableDictionary dictionary];
    }
    /* shortcut: a later initialize of this header replaces the texture. A free that is
       not followed by initialize leaves the old object until shutdown. */
    backend.textures[[NSValue valueWithPointer:header]] = taken;
}

void eu4_gfx_clear_texture(void *header) {
    if (header == NULL) {
        return;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend != nil && backend.textures != nil) {
        [backend.textures removeObjectForKey:[NSValue valueWithPointer:header]];
    }
    uint8_t *bytes = (uint8_t *)header;
    if (bytes[0x1a] != 0) {
        bytes[0x1a] = 0;
        memset(bytes + 0x10, 0, 4);
    }
    if (bytes[0x19] != 0) {
        bytes[0x19] = 0;
        memset(bytes + 0x8, 0, 4);
    }
    if (bytes[0x1b] != 0) {
        bytes[0x1b] = 0;
        memset(bytes + 0x14, 0, 4);
        memset(bytes + 0xc, 0, 4);
    }
    memset(bytes + 0x4, 0, 4);
}

void *eu4_gfx_texture_for_header(void *header) {
    if (header == NULL) {
        return NULL;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.textures == nil) {
        return NULL;
    }
    id texture = backend.textures[[NSValue valueWithPointer:header]];
    return texture != nil ? (__bridge void *)texture : NULL;
}

int eu4_gfx_decode_sampler(const void *sampler, eu4_gfx_sampler_state *out) {
    if (sampler == NULL || out == NULL) {
        return -1;
    }
    const uint8_t *bytes = (const uint8_t *)sampler;
    uint32_t wrap_s = 0, wrap_t = 0, mag = 0, min = 0, mip = 0;
    memcpy(&wrap_s, bytes + 0x18, 4);
    memcpy(&wrap_t, bytes + 0x1c, 4);
    memcpy(&mag, bytes + 0x20, 4);
    memcpy(&min, bytes + 0x24, 4);
    memcpy(&mip, bytes + 0x28, 4);
    /* GL filter enums only; anything else is an unsupported state. */
    if (mag != 0x2600 && mag != 0x2601) {
        return -1;
    }
    switch (min) {
        case 0x2600:
        case 0x2601:
        case 0x2700:
        case 0x2701:
        case 0x2702:
        case 0x2703:
            break;
        default:
            return -1;
    }
    /* The engine leaves the separate mip field at 0 when the min filter
       already selects mipmapping (or when mipmapping is disabled). */
    if (mip != 0 && mip != 0x2600 && mip != 0x2601 &&
        mip != 0x2700 && mip != 0x2701 && mip != 0x2702 && mip != 0x2703) {
        return -1;
    }
    switch (wrap_t) {
        case 0x2900:
        case 0x2901:
        case 0x812D:
        case 0x812F:
        case 0x8370:
            break;
        default:
            return -1;
    }
    switch (wrap_s) {
        case 0x2900: /* GL_CLAMP */
        case 0x2901: /* GL_REPEAT */
        case 0x812D: /* GL_CLAMP_TO_BORDER */
        case 0x812F: /* GL_CLAMP_TO_EDGE */
        case 0x8370: /* GL_MIRRORED_REPEAT */
            break;
        default:
            return -1;
    }
    out->min_filter = min;
    out->mag_filter = mag;
    out->mip_filter = mip;
    out->wrap_s = wrap_s;
    out->wrap_t = wrap_t;
    out->wrap_r = wrap_s;
    return 0;
}

void eu4_gfx_set_textures(void *handle, unsigned count, void *textures, unsigned flags,
                          unsigned base_unit, void *samplers) {
    /* The engine binds one or both contiguous ranges: bit 0 at base_unit, bit 1
       at base_unit + 0xf. Anything else is not a binding this backend can
       represent, so the call is refused instead of claiming success. */
    if (handle == NULL || textures == NULL || count == 0 ||
        (flags & 3u) == 0 || (flags & ~3u) != 0) {
        return;
    }
    void *master = NULL;
    memcpy(&master, handle, sizeof master);
    if (master == NULL) {
        return;
    }
    __unsafe_unretained EU4GfxBackend *backend = nil;
    memcpy(&backend, (uint8_t *)master + 0x180, sizeof backend);
    if (backend == nil) {
        return;
    }
    void *header = NULL;
    memcpy(&header, textures, sizeof header);
    if (header == NULL) {
        /* The original substitutes the master's default texture at +0x98. */
        memcpy(&header, (uint8_t *)master + 0x98, sizeof header);
    }
    if (backend.boundTextures == nil) {
        backend.boundTextures = [NSMutableArray array];
    }
    if (backend.boundSamplers == nil) {
        backend.boundSamplers = [NSMutableArray array];
    }
    const void *const *list = (const void *const *)textures;
    const void *const *sampler_list = (const void *const *)samplers;
    void *default_header = NULL;
    memcpy(&default_header, (uint8_t *)master + 0x98, sizeof default_header);
    /* flags bit 0 binds the contiguous range at base_unit; bit 1 binds the
       engine's second range at base_unit + 0xf. Unaffected units are kept. */
    for (unsigned pass = 0; pass < 2; pass++) {
        if ((flags & (1u << pass)) == 0) {
            continue;
        }
        unsigned start = base_unit + (pass == 0 ? 0u : 0xfu);
        for (unsigned i = 0; i < count; i++) {
            unsigned unit = start + i;
            if (unit >= 32) {
                break;
            }
            void *entry = NULL;
            memcpy(&entry, list + i, sizeof entry);
            if (entry == NULL) {
                entry = default_header;
            }
            void *sampler = NULL;
            if (sampler_list != NULL) {
                memcpy(&sampler, sampler_list + i, sizeof sampler);
            }
            while (backend.boundTextures.count <= unit) {
                [backend.boundTextures addObject:[NSValue valueWithPointer:NULL]];
            }
            while (backend.boundSamplers.count <= unit) {
                [backend.boundSamplers addObject:[NSValue valueWithPointer:NULL]];
            }
            backend.boundTextures[unit] = [NSValue valueWithPointer:entry];
            backend.boundSamplers[unit] = [NSValue valueWithPointer:sampler];
            if (unit == 0) {
                /* Only a bind that actually touches unit 0 replaces the
                   single-texture accessor. */
                void *unit0 = NULL;
                [(NSValue *)backend.boundTextures[0] getValue:&unit0];
                backend.boundTexture0 = unit0;
            }
        }
    }
}

void *eu4_gfx_bound_texture(void) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.boundTexture0 == NULL) {
        return NULL;
    }
    void *texture = eu4_gfx_texture_for_header(backend.boundTexture0);
    if (texture != NULL) {
        return texture;
    }
    /* _GfxSetTextures substitutes the master's default header (+0x98) for a
       null unit-0 entry. That header is not created by _GfxInitializeTexture;
       its Metal texture is the backend's white fallback. A registry lookup
       still wins for every real texture header. */
    if (backend.boundTexture0 == backend.textureHeader) {
        return (__bridge void *)backend.fallback;
    }
    return NULL;
}
