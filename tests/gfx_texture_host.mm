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
    uint32_t desc[5] = {4, 4, 1, 1, 0};
    memset(header, 0, sizeof header);
    memset(header + 4, 0xff, 4);
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_COMPRESSED) {
        fail("compressed");
    }
    if (header[0x28] != 0 || name_at(header) != 0xffffffffu) {
        fail("failed prepare wrote the header");
    }
    desc[3] = 0xb;
    desc[4] = 1;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_CUBEMAP) {
        fail("cubemap");
    }
    desc[4] = 2;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_FORMAT || name_at(header) != 0xffffffffu) {
        fail("kind");
    }
    desc[4] = 0;
    desc[3] = 2;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_COMPRESSED) {
        fail("format 2");
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

int main(void) {
    test_closed_branches();
    test_upload();
    test_short_bgr();
    test_bgr();
    return 0;
}
