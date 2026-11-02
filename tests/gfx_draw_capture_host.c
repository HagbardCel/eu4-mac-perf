#include "eu4_gfx_draw_capture.h"
#include "eu4_metal_events.h"

#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static void fail(const char *what) {
    fprintf(stderr, "%s\n", what);
    _exit(1);
}

static void test_read(void) {
    long page = sysconf(_SC_PAGESIZE);
    if (page < 4096) {
        fail("page");
    }
    uint8_t *base = mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("mmap");
    }
    memset(base, 0x5a, (size_t)page);
    if (munmap(base + page, (size_t)page) != 0) {
        fail("munmap");
    }
    uint8_t full[16];
    int got = -1;
    if (eu4_metal_events_read(base, full, 16, &got) != 0 || got != 16 || full[0] != 0x5a) {
        fail("full read");
    }
    uint8_t partial[64];
    memset(partial, 0, sizeof partial);
    const uint8_t *tail = base + page - 16;
    got = -1;
    if (eu4_metal_events_read(tail, partial, 64, &got) != 0 || got != 16) {
        fail("partial read was treated as complete or empty");
    }
    if (partial[0] != 0x5a || partial[16] != 0) {
        fail("partial bytes");
    }
    munmap(base, (size_t)page);
}

static void test_receipt(void) {
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 1;
    raw.retaddr = (uint64_t)(uintptr_t)&raw;
    eu4_metal_events_set_image(0, 0);
    char body[8192];
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0 || strstr(body, "\"provenance\":\"rejected\"") == NULL) {
        fail("outside image was accepted");
    }
    uintptr_t page = (uintptr_t)&raw & ~(uintptr_t)0xffff;
    eu4_metal_events_set_image(page, page + 0x20000);
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0 || strstr(body, "\"provenance\":\"game\"") == NULL) {
        fail("image caller was rejected");
    }
    if (strstr(body, "\"call_chain\":[]") != NULL) {
        fail("game caller has no call chain");
    }
    long page_size = sysconf(_SC_PAGESIZE);
    uint8_t *base = mmap(NULL, (size_t)page_size * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("context mmap");
    }
    memset(base, 0xab, (size_t)page_size);
    munmap(base + page_size, (size_t)page_size);
    uint64_t context = (uint64_t)(uintptr_t)(base + page_size - 32);
    raw.which = 2;
    raw.rdi = (uint64_t)(uintptr_t)&context;
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0) {
        fail("indexed receipt");
    }
    if (strstr(body, "\"context_complete\":false") == NULL || strstr(body, "\"context_got\":32") == NULL) {
        fail("short context was marked complete");
    }
    if (strstr(body, "\"context_want\":369") == NULL) {
        fail("indexed context size");
    }
    munmap(base, (size_t)page_size);
}

int main(void) {
    test_read();
    test_receipt();
    return 0;
}
