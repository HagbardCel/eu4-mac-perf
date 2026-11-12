#import "eu4_gfx_texture.h"

#import <Metal/Metal.h>
#import <stdio.h>
#import <string.h>
#import <sys/mman.h>
#import <unistd.h>

static void fail(const char *what) {
    fprintf(stderr, "%s\n", what);
    _exit(1);
}

typedef struct {
    uint32_t level;
    uint32_t face;
    uint32_t width;
    uint32_t height;
    uint32_t bytes;
    uint32_t pad;
    const void *pixels;
} image_record;

static uint32_t name_at(const uint8_t *header) {
    uint32_t name = 0;
    memcpy(&name, header + 4, 4);
    return name;
}

static void test_closed_branches(void) {
    uint8_t header[0x44];
    uint32_t desc[5] = {4, 4, 1, 24, 0};
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_FORMAT) {
        fail("unsupported format");
    }
    if (header[0x28] != 0 || name_at(header) != 0xffffffffu) {
        fail("failed prepare wrote the header");
    }
    desc[3] = 0xb;
    desc[4] = 1;
    desc[1] = 8;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_CUBEMAP) {
        fail("cubemap");
    }
    desc[1] = 4;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_OK) {
        fail("square cubemap");
    }
    desc[4] = 2;
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_FORMAT || name_at(header) != 0xffffffffu) {
        fail("kind");
    }
    desc[4] = 0;
    desc[3] = 2;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_OK) {
        fail("format 2");
    }
    uint32_t stored = 0;
    memcpy(&stored, header + 0x30, 4);
    if (stored != 2) {
        fail("format 2 field");
    }
    desc[3] = 6;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_OK) {
        fail("format 6");
    }
    desc[3] = 99;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_FORMAT) {
        fail("unknown format");
    }
    desc[3] = 0xb;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 1) != EU4_GFX_TEX_HEADER) {
        fail("missing images");
    }
    uint8_t pixel = 0;
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    image_record face = {0, 1, 4, 4, 0, 0, &pixel};
    if (eu4_gfx_texture_prepare(header, desc, &face, 1) != EU4_GFX_TEX_FORMAT || name_at(header) != 0xffffffffu) {
        fail("face");
    }
}

static void test_upload(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("device");
    }
    uint8_t header[0x44];
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    uint32_t desc[5] = {4, 4, 1, 0xb, 0};
    uint8_t pixels[64];
    memset(pixels, 0x11, sizeof pixels);
    pixels[0] = 0x10;
    pixels[1] = 0x20;
    pixels[2] = 0x30;
    pixels[3] = 0xff;
    image_record image = {0, 0, 4, 4, 64, 0, pixels};
    void *gpu = NULL;
    int status = eu4_gfx_texture_make((__bridge void *)device, header, desc, &image, 1, &gpu);
    if (status != EU4_GFX_TEX_OK || gpu == NULL) {
        fail("make");
    }
    if (name_at(header) != 0) {
        fail("gl name");
    }
    uint32_t format = 0;
    memcpy(&format, header + 0x30, 4);
    if (format != 0xb) {
        fail("format field");
    }
    id<MTLTexture> texture = (__bridge_transfer id<MTLTexture>)gpu;
    uint8_t out[64];
    memset(out, 0, sizeof out);
    [texture getBytes:out bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
    if (out[0] != 0x10 || out[1] != 0x20 || out[2] != 0x30 || out[3] != 0xff) {
        fail("upload bytes");
    }
}

static void test_short_bgr(void) {
    long page = sysconf(_SC_PAGESIZE);
    uint8_t *base = (uint8_t *)mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("mmap");
    }
    memset(base, 0xab, (size_t)page);
    if (munmap(base + page, (size_t)page) != 0) {
        fail("munmap");
    }
    uint8_t header[0x44];
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    uint32_t desc[5] = {2, 2, 1, 9, 0};
    image_record image = {0, 0, 2, 2, 0, 0, base + page - 8};
    if (eu4_gfx_texture_prepare(header, desc, &image, 1) != EU4_GFX_TEX_PIXELS || name_at(header) != 0xffffffffu) {
        fail("short image");
    }
    munmap(base, (size_t)page);
}

static void test_bgr(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("device");
    }
    uint8_t header[0x44];
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    uint32_t desc[5] = {2, 2, 1, 9, 0};
    uint8_t pixels[12] = {0x10, 0x20, 0x30, 0x11, 0x21, 0x31, 0x12, 0x22, 0x32, 0x13, 0x23, 0x33};
    image_record image = {0, 0, 2, 2, 12, 0, pixels};
    void *gpu = NULL;
    if (eu4_gfx_texture_make((__bridge void *)device, header, desc, &image, 1, &gpu) != EU4_GFX_TEX_OK) {
        fail("bgr make");
    }
    if (name_at(header) != 0) {
        fail("bgr name");
    }
    uint32_t internal = 0, format = 0, bpp = 0;
    memcpy(&internal, header + 0x1c, 4);
    memcpy(&format, header + 0x20, 4);
    memcpy(&bpp, header + 0x34, 4);
    if (internal != 0x8051 || format != 0x80e0 || bpp != 3) {
        fail("bgr header");
    }
    id<MTLTexture> texture = (__bridge_transfer id<MTLTexture>)gpu;
    uint8_t out[16];
    memset(out, 0, sizeof out);
    [texture getBytes:out bytesPerRow:8 fromRegion:MTLRegionMake2D(0, 0, 2, 2) mipmapLevel:0];
    const uint8_t expect[16] = {0x30, 0x20, 0x10, 0xff, 0x31, 0x21, 0x11, 0xff,
                                0x32, 0x22, 0x12, 0xff, 0x33, 0x23, 0x13, 0xff};
    if (memcmp(out, expect, sizeof expect) != 0) {
        fail("bgr channels");
    }
}

static uint32_t load_u32(const uint8_t *bytes, int offset) {
    uint32_t value = 0;
    memcpy(&value, bytes + offset, 4);
    return value;
}

static const uint8_t kBlock[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                    0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x01};
static const uint8_t kNext[16] = {0x10, 0x32, 0x54, 0x76, 0x98, 0xba, 0xdc, 0xfe,
                                   0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef};

static void expect_bc3_header(const uint8_t *header, uint32_t width, uint32_t height, uint32_t raw_mips) {
    if (name_at(header) != 0 || header[0x18] != 0 || header[0x19] != 0 || header[0x1a] != 0) {
        fail("bc3 name or flags");
    }
    if (load_u32(header, 0x1c) != 0x83f3 || load_u32(header, 0x20) != 0xa5a5a5a5u ||
        load_u32(header, 0x24) != 0xa5a5a5a5u) {
        fail("bc3 format words");
    }
    if (load_u32(header, 0x28) != width || load_u32(header, 0x2c) != height || load_u32(header, 0x30) != 5 ||
        load_u32(header, 0x34) != 1 || load_u32(header, 0x38) != width || load_u32(header, 0x3c) != height ||
        load_u32(header, 0x40) != raw_mips) {
        fail("bc3 header");
    }
}

static void test_bc3(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("device");
    }
    uint8_t header[0x44];
    uint8_t saved[0x44];
    memset(header, 0xa5, sizeof header);
    memcpy(saved, header, sizeof saved);
    uint32_t too_many[5] = {4, 4, 4, 5, 0};
    if (eu4_gfx_texture_prepare(header, too_many, NULL, 0) != EU4_GFX_TEX_LEVEL ||
        memcmp(header, saved, sizeof header) != 0) {
        fail("bc3 mip capacity");
    }

    uint8_t wide[64];
    for (int i = 0; i < 64; i++) {
        wide[i] = (uint8_t)(0x40 + i);
    }
    image_record chain[3];
    chain[0] = {0, 0, 8, 8, 64, 0, wide};
    chain[1] = {1, 0, 4, 4, 16, 0, kNext};
    chain[2] = {2, 0, 2, 2, 16, 0, kBlock};
    uint32_t two_mips[5] = {8, 8, 2, 5, 0};
    if (eu4_gfx_texture_prepare(header, two_mips, chain, 3) != EU4_GFX_TEX_LEVEL ||
        memcmp(header, saved, sizeof header) != 0) {
        fail("bc3 level outside the declared count");
    }

    memset(header, 0xa5, sizeof header);
    void *gpu = NULL;
    if (eu4_gfx_texture_make((__bridge void *)device, header, two_mips, chain, 2, &gpu) != EU4_GFX_TEX_OK ||
        gpu == NULL) {
        fail("bc3 chain");
    }
    expect_bc3_header(header, 8, 8, 2);
    id<MTLTexture> texture = (__bridge_transfer id<MTLTexture>)gpu;
    if (texture.pixelFormat != MTLPixelFormatBC3_RGBA || texture.mipmapLevelCount != 2) {
        fail("bc3 chain texture");
    }
    uint8_t out[64];
    memset(out, 0, sizeof out);
    [texture getBytes:out bytesPerRow:32 fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
    if (memcmp(out, wide, 64) != 0) {
        fail("bc3 stride 32");
    }
    memset(out, 0, sizeof out);
    [texture getBytes:out bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:1];
    if (memcmp(out, kNext, 16) != 0) {
        fail("bc3 stride 16");
    }

    memset(header, 0xa5, sizeof header);
    uint32_t one[5] = {4, 4, 1, 5, 0};
    image_record image = {0, 0, 4, 4, 16, 0, kBlock};
    gpu = NULL;
    if (eu4_gfx_texture_make((__bridge void *)device, header, one, &image, 1, &gpu) != EU4_GFX_TEX_OK) {
        fail("bc3 mip");
    }
    expect_bc3_header(header, 4, 4, 1);
    texture = (__bridge_transfer id<MTLTexture>)gpu;
    if (texture.pixelFormat != MTLPixelFormatBC3_RGBA) {
        fail("bc3 format");
    }
    memset(out, 0, 16);
    [texture getBytes:out bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
    if (memcmp(out, kBlock, 16) != 0) {
        fail("bc3 block");
    }

    memset(header, 0xa5, sizeof header);
    uint32_t zero_mips[5] = {4, 4, 0, 5, 0};
    gpu = NULL;
    if (eu4_gfx_texture_make((__bridge void *)device, header, zero_mips, &image, 1, &gpu) != EU4_GFX_TEX_OK) {
        fail("bc3 raw mips");
    }
    expect_bc3_header(header, 4, 4, 0);
    texture = (__bridge_transfer id<MTLTexture>)gpu;
    if (texture.mipmapLevelCount != 1) {
        fail("bc3 effective mip");
    }

    memset(header, 0xa5, sizeof header);
    uint32_t tiny[5] = {1, 1, 1, 5, 0};
    image_record dot = {0, 0, 1, 1, 16, 0, kBlock};
    gpu = NULL;
    if (eu4_gfx_texture_make((__bridge void *)device, header, tiny, &dot, 1, &gpu) != EU4_GFX_TEX_OK) {
        fail("bc3 small mip");
    }
    expect_bc3_header(header, 1, 1, 1);
    texture = (__bridge_transfer id<MTLTexture>)gpu;
    memset(out, 0, 16);
    [texture getBytes:out bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0];
    if (memcmp(out, kBlock, 16) != 0) {
        fail("bc3 small block");
    }

    memset(header, 0xa5, sizeof header);
    memcpy(saved, header, sizeof saved);
    image.bytes = 32;
    if (eu4_gfx_texture_prepare(header, one, &image, 1) != EU4_GFX_TEX_SIZE ||
        memcmp(header, saved, sizeof header) != 0) {
        fail("bc3 byte count");
    }
    image.bytes = 16;

    long page = sysconf(_SC_PAGESIZE);
    uint8_t *base = (uint8_t *)mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("bc3 map");
    }
    memset(base, 0xab, (size_t)page);
    if (munmap(base + page, (size_t)page) != 0) {
        fail("bc3 unmap");
    }
    image_record short_image = {0, 0, 4, 4, 16, 0, base + page - 8};
    if (eu4_gfx_texture_prepare(header, one, &short_image, 1) != EU4_GFX_TEX_PIXELS ||
        memcmp(header, saved, sizeof header) != 0) {
        fail("bc3 short source");
    }
    munmap(base, (size_t)page);

    memset(header, 0xa5, sizeof header);
    eu4_gfx_test_no_bc(1);
    gpu = NULL;
    int blocked = eu4_gfx_texture_make((__bridge void *)device, header, one, &image, 1, &gpu);
    eu4_gfx_test_no_bc(0);
    if (blocked != EU4_GFX_TEX_METAL || gpu != NULL) {
        fail("bc3 capability");
    }
}

static void test_set_textures(void) {
    uint8_t object[0x198];
    uint8_t saved[0x198];
    memset(object, 0x5a, sizeof object);
    memcpy(saved, object, sizeof saved);
    void *handle = object;
    void *invalid = reinterpret_cast<void *>(1);
    eu4_gfx_set_textures(&handle, 0, invalid, 0, 0, invalid);
    if (memcmp(object, saved, sizeof saved) != 0) {
        fail("set textures wrote the object");
    }
    eu4_gfx_set_textures(&handle, 2, invalid, 3, 3, invalid);
    if (memcmp(object, saved, sizeof saved) != 0) {
        fail("set textures wrote the object with flags");
    }
}

int main(void) {
    test_closed_branches();
    test_upload();
    test_short_bgr();
    test_bgr();
    test_bc3();
    test_set_textures();
    return 0;
}
