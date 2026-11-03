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

static int block_span(uint32_t width, uint32_t height, uint32_t *span) {
    uint32_t blocks_wide = (width + 3) / 4;
    uint32_t blocks_high = (height + 3) / 4;
    if (blocks_wide == 0 || blocks_high > UINT32_MAX / blocks_wide) {
        return -1;
    }
    uint32_t blocks = blocks_wide * blocks_high;
    if (blocks > UINT32_MAX / 16) {
        return -1;
    }
    uint32_t bytes = blocks * 16;
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
    if (info.kind == 1) {
        return EU4_GFX_TEX_CUBEMAP;
    }
    if (info.kind != 0) {
        return EU4_GFX_TEX_FORMAT;
    }
    int compressed_bc3 = info.format == 5;
    if (info.format >= 1 && info.format <= 7 && !compressed_bc3) {
        return EU4_GFX_TEX_COMPRESSED;
    }
    int bgra = info.format == 0xb || info.format == 0xd;
    int srgb = info.format == 0xc || info.format == 0xe;
    int bgr = info.format == 9;
    if (!compressed_bc3 && !bgra && !srgb && !bgr) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (!compressed_bc3 && (info.format < 7 || info.format > 23)) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (info.width == 0 || info.height == 0 || info.width > 16384 || info.height > 16384) {
        return EU4_GFX_TEX_SIZE;
    }
    if (count > 16) {
        return EU4_GFX_TEX_LEVEL;
    }
    uint32_t raw_mips = info.mips;
    uint32_t mips = raw_mips == 0 ? 1 : raw_mips;
    if (compressed_bc3 && mips > mip_capacity(info.width, info.height)) {
        return EU4_GFX_TEX_LEVEL;
    }
    const tex_image *records = (const tex_image *)images;
    for (uint32_t i = 0; i < count; i++) {
        if (records[i].level >= 16) {
            return EU4_GFX_TEX_LEVEL;
        }
        if (compressed_bc3) {
            if (records[i].level >= mips) {
                return EU4_GFX_TEX_LEVEL;
            }
        } else if (records[i].level + 1 > mips) {
            mips = records[i].level + 1;
        }
    }
    if (!compressed_bc3 && mips > 16) {
        return EU4_GFX_TEX_LEVEL;
    }
    uint32_t bpp = compressed_bc3 ? 1 : kBytes[info.format - 7];
    for (uint32_t i = 0; i < count; i++) {
        uint32_t expect_w = mip_size(info.width, records[i].level);
        uint32_t expect_h = mip_size(info.height, records[i].level);
        if (records[i].face != 0) {
            free_staged(staged, i);
            return EU4_GFX_TEX_FORMAT;
        }
        if (records[i].width != expect_w || records[i].height != expect_h || records[i].pixels == NULL) {
            free_staged(staged, i);
            return records[i].pixels == NULL ? EU4_GFX_TEX_PIXELS : EU4_GFX_TEX_SIZE;
        }
        uint8_t *copy = NULL;
        int staged_status;
        if (compressed_bc3) {
            uint32_t span = 0;
            if (block_span(records[i].width, records[i].height, &span) != 0 || records[i].bytes != span) {
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
    if (compressed_bc3) {
        /* Ordinal 5 writes only the DXT5 internal format. +0x20 and +0x24 stay put. */
        write_u32(bytes, 0x1c, 0x83f3);
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
    write_u32(bytes, 0x40, compressed_bc3 ? raw_mips : mips);
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
    uint8_t *staged[16];
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
    int compressed_bc3 = format == 5;
    uint32_t level_count = mips;
    if (compressed_bc3) {
        uint32_t raw = 0;
        memcpy(&raw, (const uint8_t *)desc + 8, 4);
        level_count = raw == 0 ? 1 : raw;
        if (g_no_bc || ![(__bridge id<MTLDevice>)device supportsBCTextureCompression]) {
            free_staged(staged, count);
            return EU4_GFX_TEX_METAL;
        }
    }
    MTLPixelFormat pixel = compressed_bc3 ? MTLPixelFormatBC3_RGBA
                           : format == 9   ? MTLPixelFormatRGBA8Unorm
                           : (format == 0xc || format == 0xe) ? MTLPixelFormatBGRA8Unorm_sRGB
                                                              : MTLPixelFormatBGRA8Unorm;
    MTLTextureDescriptor *metal = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
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
        NSUInteger row = compressed_bc3 ? (NSUInteger)((records[i].width + 3) / 4) * 16
                                        : (NSUInteger)records[i].width * 4;
        if (format == 9) {
            expanded = expand_bgr(staged[i], records[i].width, records[i].height);
            if (expanded == NULL) {
                free_staged(staged, count);
                return EU4_GFX_TEX_PIXELS;
            }
            upload = expanded;
        }
        [texture replaceRegion:MTLRegionMake2D(0, 0, records[i].width, records[i].height)
                   mipmapLevel:records[i].level
                     withBytes:upload
                   bytesPerRow:row];
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

void eu4_gfx_initialize_texture(void *ignored, void *header, const void *desc, const void *images,
                                uint32_t count) {
    const void *caller = __builtin_return_address(0);
    (void)ignored;
    eu4_gfx_require_thread("_GfxInitializeTexture", caller);
    void *gpu = NULL;
    int status = eu4_gfx_texture_make(eu4_gfx_metal_device(), header, desc, images, count, &gpu);
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

void eu4_gfx_set_textures(void *handle, unsigned count, void *textures, unsigned flags,
                          unsigned base_unit, void *samplers) {
    (void)handle;
    (void)count;
    (void)textures;
    (void)flags;
    (void)base_unit;
    (void)samplers;
}
