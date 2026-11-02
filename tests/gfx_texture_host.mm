#import "eu4_gfx_texture.h"

#import <Metal/Metal.h>
#import <stdio.h>
#import <string.h>
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

static void test_closed_branches(void) {
    uint8_t header[0x44];
    uint32_t desc[5] = {4, 4, 1, 1, 0};
    memset(header, 0, sizeof header);
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_COMPRESSED) {
        fail("compressed");
    }
    if (header[0x28] != 0) {
        fail("failed prepare wrote the header");
    }
    desc[3] = 0xb;
    desc[4] = 1;
    if (eu4_gfx_texture_prepare(header, desc, NULL, 0) != EU4_GFX_TEX_CUBEMAP) {
        fail("cubemap");
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
}

static void test_upload(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("device");
    }
    uint8_t header[0x44];
    memset(header, 0, sizeof header);
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
    uint32_t name = 1;
    memcpy(&name, header + 4, 4);
    if (name != 0) {
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

int main(void) {
    test_closed_branches();
    test_upload();
    return 0;
}
