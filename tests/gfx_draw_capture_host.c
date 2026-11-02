#include "eu4_gfx_buffers.h"
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

static void test_executable_ranges(void) {
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 1;
    uintptr_t base = 0x100000000ULL;
    eu4_metal_events_set_image(base, base);
    eu4_metal_events_add_executable(base + 0x1000, base + 0x2000);
    eu4_metal_events_add_executable(base + 0x4000, base + 0x5000);
    char body[8192];
    raw.retaddr = base + 0x1800;
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0 || strstr(body, "\"provenance\":\"game\"") == NULL) {
        fail("executable range rejected");
    }
    if (strstr(body, "\"caller_rva\":\"0x1800\"") == NULL) {
        fail("rva base");
    }
    raw.retaddr = base + 0x3000;
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0 || strstr(body, "\"provenance\":\"rejected\"") == NULL) {
        fail("hole accepted");
    }
    raw.retaddr = base + 0x800;
    if (eu4_gfx_draw_receipt(&raw, body, (int)sizeof body) != 0 || strstr(body, "\"provenance\":\"rejected\"") == NULL) {
        fail("non-executable address accepted");
    }
}

static void expect_draw(const eu4_gfx_draw_raw *raw, const char *needle, const char *absent) {
    char body[8192];
    if (eu4_gfx_draw_receipt(raw, body, (int)sizeof body) != 0 || strstr(body, needle) == NULL) {
        fail(needle);
    }
    if (absent != NULL && strstr(body, absent) != NULL) {
        fail(absent);
    }
}

static void test_draw_fields(void) {
    long page_size = sysconf(_SC_PAGESIZE);
    uint8_t *base = mmap(NULL, (size_t)page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("draw mmap");
    }
    memset(base, 0, (size_t)page_size);
    uint32_t fallback = 9;
    memcpy(base + 0x64, &fallback, sizeof fallback);
    base[0x170] = 0;
    uint64_t master = (uint64_t)(uintptr_t)base;
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 1;
    raw.rsi = 4;
    raw.rdi = (uint64_t)(uintptr_t)&master;
    uintptr_t image = (uintptr_t)&raw & ~(uintptr_t)0xffff;
    eu4_metal_events_set_image(image, image + 0x20000);
    raw.retaddr = (uint64_t)(uintptr_t)&raw;
    expect_draw(&raw, "\"shader_ptr\":\"0x0\"", NULL);
    expect_draw(&raw, "\"effective_count\":4", "\"effective_count\":9");
    expect_draw(&raw, "\"attrib_dirty\":0", NULL);
    expect_draw(&raw, "\"context_complete\":true", NULL);
    uint64_t shader = 0x20;
    memcpy(base + 0x70, &shader, sizeof shader);
    raw.rsi = 0;
    expect_draw(&raw, "\"shader_ptr\":\"0x20\"", NULL);
    expect_draw(&raw, "\"effective_count\":9", NULL);
    munmap(base, (size_t)page_size);

    uint8_t *span = mmap(NULL, (size_t)page_size * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (span == MAP_FAILED) {
        fail("short draw mmap");
    }
    memset(span, 0xff, (size_t)page_size);
    munmap(span + page_size, (size_t)page_size);
    master = (uint64_t)(uintptr_t)(span + page_size - 0x70);
    raw.rsi = 5;
    raw.rdi = (uint64_t)(uintptr_t)&master;
    expect_draw(&raw, "\"shader_ptr\":null", "\"shader_ptr\":\"0x0\"");
    expect_draw(&raw, "\"effective_count\":5", NULL);
    expect_draw(&raw, "\"attrib_dirty\":null", NULL);
    expect_draw(&raw, "\"context_got\":112", NULL);
    expect_draw(&raw, "\"context_complete\":false", NULL);
    master = (uint64_t)(uintptr_t)(span + page_size - 0x60);
    raw.rsi = 0;
    raw.rdi = (uint64_t)(uintptr_t)&master;
    expect_draw(&raw, "\"effective_count\":null", NULL);
    expect_draw(&raw, "\"shader_ptr\":null", NULL);
    expect_draw(&raw, "\"context_got\":96", NULL);
    munmap(span, (size_t)page_size);
}

static void expect_has(const char *body, const char *needle) {
    if (strstr(body, needle) == NULL) {
        fail(needle);
    }
}

static void expect_absent(const char *body, const char *needle) {
    if (strstr(body, needle) != NULL) {
        fail(needle);
    }
}

static uint8_t *map_bytes(size_t bytes) {
    uint8_t *base = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
        fail("selection mmap");
    }
    memset(base, 0, bytes);
    return base;
}

static void arm_draw(eu4_gfx_draw_raw *raw, uint64_t *slot, uint8_t *master, uint64_t rva, uint64_t count,
                     uint64_t shader) {
    memset(raw, 0, sizeof *raw);
    raw->which = 1;
    raw->rsi = count;
    *slot = (uint64_t)(uintptr_t)master;
    raw->rdi = (uint64_t)(uintptr_t)slot;
    uintptr_t image = 0x100000000ULL;
    eu4_metal_events_set_image(image, image + 0x2000000ULL);
    raw->retaddr = image + rva;
    uint32_t mode = 5;
    uint32_t fallback = 0;
    memcpy(master + 8, &mode, sizeof mode);
    memcpy(master + 0x64, &fallback, sizeof fallback);
    memcpy(master + 0x70, &shader, sizeof shader);
    master[0x128] = 1;
    master[0x170] = 1;
}

static int call_draw(const eu4_gfx_draw_raw *raw, char *body) {
    memset(body, 'Z', 8192);
    int result = eu4_gfx_draw_receipt(raw, body, 8192);
    if (result < 0) {
        fail("selection receipt");
    }
    return result;
}

static void test_selection(void) {
    long page = sysconf(_SC_PAGESIZE);
    uint8_t *master = map_bytes((size_t)page);
    uint8_t *shader = map_bytes((size_t)page);
    memset(shader, 0x11, 0x78);
    uint64_t slot = 0;
    eu4_gfx_draw_raw raw;
    char body[8192];
    eu4_gfx_draw_selection_reset();
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 0, (uint64_t)(uintptr_t)shader);
    if (call_draw(&raw, body) != 0) {
        fail("unbound draw skipped");
    }
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"invocation_id\":0,");
    expect_has(body, "\"skipped_count\":0,");

    eu4_gfx_buffers_bind_thread();
    eu4_gfx_draw_selection_reset();
    for (uint64_t count = 0; count < 3; count++) {
        arm_draw(&raw, &slot, master, 0xedfcd1ULL, count, (uint64_t)(uintptr_t)shader);
        if (call_draw(&raw, body) != 1 || body[0] != 'Z') {
            fail("nonvisible draw did not return");
        }
    }
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 3, (uint64_t)(uintptr_t)shader);
    if (call_draw(&raw, body) != 0) {
        fail("count 3 skipped");
    }
    expect_has(body, "\"selection\":\"candidate\"");
    expect_has(body, "\"invocation_id\":4,");
    expect_has(body, "\"skipped_count\":3,");
    expect_has(body, "\"shader_object_want\":120");
    expect_has(body, "\"shader_object_got\":120");
    expect_has(body, "\"uniform_dirty\":1");
    expect_has(body, "\"attrib_dirty\":1");
    expect_absent(body, "\"shader_object\":null");

    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 4, 0);
    if (call_draw(&raw, body) != 0) {
        fail("null shader skipped");
    }
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"shader_ptr\":\"0x0\"");
    expect_has(body, "\"shader_object_got\":0");
    expect_has(body, "\"shader_object\":null");
    expect_has(body, "\"skipped_count\":3,");
    expect_has(body, "\"invocation_id\":5,");

    uint8_t *span = map_bytes((size_t)page * 2);
    munmap(span + page, (size_t)page);
    uint8_t *short_shader = span + page - 16;
    memset(short_shader, 0x22, 16);
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 6, (uint64_t)(uintptr_t)short_shader);
    if (call_draw(&raw, body) != 0) {
        fail("short shader skipped");
    }
    expect_has(body, "\"selection\":\"candidate\"");
    expect_has(body, "\"shader_object_got\":16");
    expect_has(body, "\"shader_object\":null");
    expect_has(body, "\"skipped_count\":3,");

    memset(&raw, 0, sizeof raw);
    raw.which = 1;
    slot = (uint64_t)(uintptr_t)(span + page - 0x60);
    raw.rdi = (uint64_t)(uintptr_t)&slot;
    raw.retaddr = 0x100000000ULL + 0xedfcd1ULL;
    if (call_draw(&raw, body) != 0) {
        fail("short master skipped");
    }
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"effective_count\":null");
    expect_has(body, "\"shader_ptr\":null");
    expect_has(body, "\"skipped_count\":3,");

    arm_draw(&raw, &slot, master, 0x1000ULL, 0, (uint64_t)(uintptr_t)shader);
    if (call_draw(&raw, body) != 0) {
        fail("other caller skipped");
    }
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"caller_rva\":\"0x1000\"");
    expect_has(body, "\"skipped_count\":3,");

    uint32_t negative = 0xffffffffu;
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 0, (uint64_t)(uintptr_t)shader);
    memcpy(master + 0x64, &negative, sizeof negative);
    if (call_draw(&raw, body) != 0) {
        fail("negative count skipped");
    }
    expect_has(body, "\"effective_count\":-1");
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"skipped_count\":3,");

    uint32_t lines = 4;
    memcpy(master + 8, &lines, sizeof lines);
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 8, (uint64_t)(uintptr_t)shader);
    memcpy(master + 8, &lines, sizeof lines);
    if (call_draw(&raw, body) != 0) {
        fail("other mode skipped");
    }
    expect_has(body, "\"selection\":\"ineligible_stop\"");
    expect_has(body, "\"skipped_count\":3,");

    raw.which = 2;
    raw.rdi = 0;
    if (call_draw(&raw, body) != 0) {
        fail("indexed receipt");
    }
    expect_has(body, "\"invocation_id\":1,");
    expect_absent(body, "\"selection\"");

    eu4_gfx_draw_selection_reset();
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 3, (uint64_t)(uintptr_t)shader);
    if (call_draw(&raw, body) != 0) {
        fail("reset did not restore the counters");
    }
    expect_has(body, "\"invocation_id\":1,");
    expect_has(body, "\"skipped_count\":0,");
    expect_has(body, "\"selection\":\"candidate\"");

    eu4_gfx_draw_selection_reset();
    for (int i = 0; i < 256; i++) {
        arm_draw(&raw, &slot, master, 0xedfcd1ULL, 0, (uint64_t)(uintptr_t)shader);
        if (call_draw(&raw, body) != 1) {
            fail("budget skip");
        }
    }
    arm_draw(&raw, &slot, master, 0xedfcd1ULL, 0, (uint64_t)(uintptr_t)shader);
    if (call_draw(&raw, body) != 0) {
        fail("budget continued");
    }
    expect_has(body, "\"selection\":\"budget_exhausted\"");
    expect_has(body, "\"skipped_count\":256,");
    expect_has(body, "\"invocation_id\":257,");

    munmap(master, (size_t)page);
    munmap(shader, (size_t)page);
    munmap(span, (size_t)page);
}

int main(void) {
    test_read();
    test_receipt();
    test_executable_ranges();
    test_draw_fields();
    test_selection();
    return 0;
}
