#import "eu4_gfx_texture.h"

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <stdio.h>
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

static void write_u32(uint8_t *header, int offset, uint32_t value) {
    memcpy(header + offset, &value, sizeof value);
}

static uint32_t mip_size(uint32_t base, uint32_t level) {
    uint32_t value = base >> level;
    return value == 0 ? 1 : value;
}

int eu4_gfx_texture_prepare(void *header, const void *desc, const void *images, uint32_t count) {
    if (header == NULL || desc == NULL || (count > 0 && images == NULL)) {
        return EU4_GFX_TEX_HEADER;
    }
    tex_desc info;
    memcpy(&info, desc, sizeof info);
    if (info.kind == 1) {
        return EU4_GFX_TEX_CUBEMAP;
    }
    if (info.format >= 1 && info.format <= 7) {
        return EU4_GFX_TEX_COMPRESSED;
    }
    int bgra = info.format == 0xb || info.format == 0xd;
    int srgb = info.format == 0xc || info.format == 0xe;
    if (!bgra && !srgb) {
        return EU4_GFX_TEX_FORMAT;
    }
    if (info.width == 0 || info.height == 0 || info.width > 16384 || info.height > 16384) {
        return EU4_GFX_TEX_SIZE;
    }
    if (count > 16) {
        return EU4_GFX_TEX_LEVEL;
    }
    uint32_t mips = info.mips == 0 ? 1 : info.mips;
    const tex_image *records = (const tex_image *)images;
    for (uint32_t i = 0; i < count; i++) {
        if (records[i].level >= 16) {
            return EU4_GFX_TEX_LEVEL;
        }
        if (records[i].level + 1 > mips) {
            mips = records[i].level + 1;
        }
    }
    if (mips > 16) {
        return EU4_GFX_TEX_LEVEL;
    }
    for (uint32_t i = 0; i < count; i++) {
        uint32_t expect_w = mip_size(info.width, records[i].level);
        uint32_t expect_h = mip_size(info.height, records[i].level);
        if (records[i].width != expect_w || records[i].height != expect_h || records[i].pixels == NULL) {
            return records[i].pixels == NULL ? EU4_GFX_TEX_PIXELS : EU4_GFX_TEX_SIZE;
        }
    }
    uint8_t *bytes = (uint8_t *)header;
    bytes[0x18] = 0;
    bytes[0x19] = 0;
    bytes[0x1a] = 0;
    write_u32(bytes, 0x1c, bgra ? 0x8058 : 0x8c43);
    write_u32(bytes, 0x20, 0x80e1);
    write_u32(bytes, 0x24, 0x1401);
    write_u32(bytes, 0x28, info.width);
    write_u32(bytes, 0x2c, info.height);
    write_u32(bytes, 0x30, info.format);
    write_u32(bytes, 0x34, kBytes[info.format - 7]);
    write_u32(bytes, 0x38, info.width);
    write_u32(bytes, 0x3c, info.height);
    write_u32(bytes, 0x40, mips);
    return EU4_GFX_TEX_OK;
}

int eu4_gfx_texture_make(void *device, void *header, const void *desc, const void *images, uint32_t count,
                         void **out_texture) {
    if (out_texture != NULL) {
        *out_texture = NULL;
    }
    int status = eu4_gfx_texture_prepare(header, desc, images, count);
    if (status != 0) {
        return status;
    }
    if (device == NULL || out_texture == NULL) {
        return EU4_GFX_TEX_BACKEND;
    }
    uint32_t width = 0, height = 0, mips = 0, format = 0;
    memcpy(&width, (uint8_t *)header + 0x28, 4);
    memcpy(&height, (uint8_t *)header + 0x2c, 4);
    memcpy(&format, (uint8_t *)header + 0x30, 4);
    memcpy(&mips, (uint8_t *)header + 0x40, 4);
    MTLPixelFormat pixel = (format == 0xc || format == 0xe) ? MTLPixelFormatBGRA8Unorm_sRGB
                                                            : MTLPixelFormatBGRA8Unorm;
    MTLTextureDescriptor *metal = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
                                                                                     width:width
                                                                                    height:height
                                                                                 mipmapped:mips > 1];
    metal.mipmapLevelCount = mips;
    metal.usage = MTLTextureUsageShaderRead;
    metal.storageMode = MTLStorageModeShared;
    id<MTLTexture> texture = [(__bridge id<MTLDevice>)device newTextureWithDescriptor:metal];
    if (texture == nil) {
        return EU4_GFX_TEX_METAL;
    }
    const tex_image *records = (const tex_image *)images;
    for (uint32_t i = 0; i < count; i++) {
        NSUInteger row = (NSUInteger)records[i].width * 4;
        [texture replaceRegion:MTLRegionMake2D(0, 0, records[i].width, records[i].height)
                   mipmapLevel:records[i].level
                     withBytes:records[i].pixels
                   bytesPerRow:row];
    }
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
    /* shortcut: kept until backend shutdown. _GfxDestroyTexture stays original and the
       GL name at +4 is 0, so its delete does not name this texture. Upgrade when destroy blocks. */
    backend.textures[[NSValue valueWithPointer:header]] = taken;
}
