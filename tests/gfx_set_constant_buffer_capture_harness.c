#include "eu4_gfx_capture_detour.h"
#include "eu4_gfx_set_constant_buffer_capture.h"
#include "gfx_capture_test_state.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CTX_SPAN 0x139u
#define OBSERVATION_START 200u
#define DIRTY_OFFSET 0x128u
#define SLOT_STRIDE 16u
#define HANDLE_LENGTH 8u
#define BUFFER_LENGTH 4u
#define CODE_LENGTH 75u

extern void gfx_set_constant_buffer_contract_stub(void);

gfx_capture_test_state gfx_capture_test_seed;
gfx_capture_test_state gfx_capture_test_capture;
uint64_t gfx_capture_test_pre_call_rsp;
uint64_t gfx_capture_test_capture_rsp;
void *gfx_capture_test_target;

typedef struct {
    size_t page;
    uint8_t *ctx_map;
    uint8_t *ctx;
    uint8_t *handle_map;
    uint8_t *handle;
    uint8_t *buffer_map;
    uint8_t *buffer;
    size_t buffer_map_size;
    int buffer_path;
} fixture_memory;

typedef struct {
    uint8_t ctx[0x200];
    uint64_t handle;
    uint8_t buffer[BUFFER_LENGTH];
} thread_fixture;

typedef struct {
    uint8_t ctx[CTX_SPAN];
    uint8_t handle[HANDLE_LENGTH];
    uint8_t buffer[BUFFER_LENGTH];
    int buffer_path;
} memory_snapshot;

static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}

static uint8_t pattern(size_t offset) {
    return (uint8_t)((offset * 55u + 17u) & 0xffu);
}

static void fill_ctx(uint8_t *ctx) {
    for (size_t i = 0; i < CTX_SPAN; i++) {
        ctx[i] = pattern(i);
    }
}

static void reset_memory(fixture_memory *memory) {
    fill_ctx(memory->ctx);
    uint64_t ctx_value = (uint64_t)(uintptr_t)memory->ctx;
    memcpy(memory->handle, &ctx_value, HANDLE_LENGTH);
    if (memory->buffer_path) {
        uint32_t length = 13;
        memcpy(memory->buffer, &length, BUFFER_LENGTH);
    }
}

static void snapshot_memory(const fixture_memory *memory,
                            memory_snapshot *snapshot) {
    memset(snapshot, 0, sizeof(*snapshot));
    memcpy(snapshot->ctx, memory->ctx, CTX_SPAN);
    memcpy(snapshot->handle, memory->handle, HANDLE_LENGTH);
    snapshot->buffer_path = memory->buffer_path;
    if (memory->buffer_path) {
        memcpy(snapshot->buffer, memory->buffer, BUFFER_LENGTH);
    }
}

static int memory_equal(const memory_snapshot *a, const memory_snapshot *b) {
    if (a->buffer_path != b->buffer_path ||
        memcmp(a->ctx, b->ctx, CTX_SPAN) != 0 ||
        memcmp(a->handle, b->handle, HANDLE_LENGTH) != 0) {
        return 0;
    }
    return !a->buffer_path ||
           memcmp(a->buffer, b->buffer, BUFFER_LENGTH) == 0;
}

static void release_memory(fixture_memory *memory) {
    if (memory->ctx_map != NULL) {
        (void)munmap(memory->ctx_map, 2 * memory->page);
    }
    if (memory->handle_map != NULL) {
        (void)munmap(memory->handle_map, 2 * memory->page);
    }
    if (memory->buffer_map != NULL) {
        (void)munmap(memory->buffer_map, memory->buffer_map_size);
    }
    memset(memory, 0, sizeof(*memory));
}

static int setup_memory(fixture_memory *memory, int buffer_path) {
    memset(memory, 0, sizeof(*memory));
    long page_value = sysconf(_SC_PAGESIZE);
    if (page_value <= 0) {
        return fail("page size is unavailable");
    }
    memory->page = (size_t)page_value;
    memory->buffer_path = buffer_path;

    memory->ctx_map = mmap(NULL, 2 * memory->page, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANON, -1, 0);
    if (memory->ctx_map == MAP_FAILED) {
        memory->ctx_map = NULL;
        return fail("cannot map context");
    }
    if (mprotect(memory->ctx_map + memory->page, memory->page, PROT_NONE) != 0) {
        release_memory(memory);
        return fail("cannot guard context");
    }
    memory->ctx = memory->ctx_map + memory->page - CTX_SPAN;
    fill_ctx(memory->ctx);

    memory->handle_map = mmap(NULL, 2 * memory->page, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANON, -1, 0);
    if (memory->handle_map == MAP_FAILED) {
        memory->handle_map = NULL;
        release_memory(memory);
        return fail("cannot map handle");
    }
    if (mprotect(memory->handle_map + memory->page, memory->page, PROT_NONE) !=
        0) {
        release_memory(memory);
        return fail("cannot guard handle");
    }
    memory->handle = memory->handle_map + memory->page - HANDLE_LENGTH;
    uint64_t ctx_value = (uint64_t)(uintptr_t)memory->ctx;
    memcpy(memory->handle, &ctx_value, HANDLE_LENGTH);

    if (buffer_path) {
        memory->buffer_map = mmap(NULL, 2 * memory->page,
                                  PROT_READ | PROT_WRITE,
                                  MAP_PRIVATE | MAP_ANON, -1, 0);
        if (memory->buffer_map == MAP_FAILED) {
            memory->buffer_map = NULL;
            release_memory(memory);
            return fail("cannot map buffer");
        }
        if (mprotect(memory->buffer_map + memory->page, memory->page,
                     PROT_NONE) != 0) {
            release_memory(memory);
            return fail("cannot guard buffer");
        }
        memory->buffer_map_size = 2 * memory->page;
        memory->buffer =
            memory->buffer_map + memory->page - BUFFER_LENGTH;
        uint32_t length = 13;
        memcpy(memory->buffer, &length, BUFFER_LENGTH);
    } else {
        memory->buffer_map =
            mmap(NULL, memory->page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (memory->buffer_map == MAP_FAILED) {
            memory->buffer_map = NULL;
            release_memory(memory);
            return fail("cannot map PROT_NONE buffer");
        }
        memory->buffer_map_size = memory->page;
        memory->buffer = memory->buffer_map;
    }
    return 0;
}

static void seed_for(const fixture_memory *memory, unsigned slot,
                     int buffer_path) {
    memset(&gfx_capture_test_seed, 0, sizeof(gfx_capture_test_seed));
    gfx_capture_test_seed.rax = 0xa0a0a0a0a0a0a0a0ull;
    gfx_capture_test_seed.rbx = 0xb0b0b0b0b0b0b0b0ull;
    gfx_capture_test_seed.rcx = 0xdeadbeef00000000ull | slot;
    gfx_capture_test_seed.rdx = 0xd0d0d0d0d0d0d0d0ull;
    gfx_capture_test_seed.rsi = (uint64_t)(uintptr_t)memory->buffer;
    gfx_capture_test_seed.rdi = (uint64_t)(uintptr_t)memory->handle;
    gfx_capture_test_seed.rbp = 0x5151515151515151ull;
    gfx_capture_test_seed.r8 = 0xf0f0f0f0f0f0f0f0ull;
    gfx_capture_test_seed.r9 = 0xfeedface00000000ull |
                                (buffer_path ? 0xffffffffu : 13u);
    gfx_capture_test_seed.r10 = 0x1313131313131313ull;
    gfx_capture_test_seed.r11 = 0x1414141414141414ull;
    gfx_capture_test_seed.r12 = 0x1515151515151515ull;
    gfx_capture_test_seed.r13 = 0x1616161616161616ull;
    gfx_capture_test_seed.r14 = 0x1717171717171717ull;
    gfx_capture_test_seed.r15 = 0x1818181818181818ull;
    gfx_capture_test_seed.flags = 0xad7ull;
    gfx_capture_test_seed.mxcsr = 0x1f80u;
    for (int i = 0; i < 16; i++) {
        for (int j = 0; j < 16; j++) {
            gfx_capture_test_seed.xmm[i][j] =
                (uint8_t)(0x40 + i * 16 + j);
        }
    }
}

static int check_post(const fixture_memory *memory, unsigned slot,
                      int buffer_path) {
    if (gfx_capture_test_capture.rax !=
        (uint64_t)(uintptr_t)memory->ctx) {
        return fail("outcome RAX is not the context base");
    }
    uint32_t words = 0;
    memcpy(&words, memory->ctx + 0xc8u + slot * SLOT_STRIDE,
           sizeof(words));
    if (words != 4) {
        return fail("word count was not four");
    }
    uint64_t pointer = 0;
    memcpy(&pointer, memory->ctx + 0xd0u + slot * SLOT_STRIDE,
           sizeof(pointer));
    if (pointer != (uint64_t)(uintptr_t)memory->buffer) {
        return fail("buffer slot does not hold the carrier pointer");
    }
    if (memory->ctx[DIRTY_OFFSET] != 1) {
        return fail("dirty byte was not set");
    }
    for (size_t i = 184; i < OBSERVATION_START; i++) {
        if (memory->ctx[i] != pattern(i)) {
            return fail("canary-before changed");
        }
    }
    for (size_t i = 297; i < CTX_SPAN; i++) {
        if (memory->ctx[i] != pattern(i)) {
            return fail("canary-after changed");
        }
    }
    uint64_t handle = 0;
    memcpy(&handle, memory->handle, HANDLE_LENGTH);
    if (handle != (uint64_t)(uintptr_t)memory->ctx) {
        return fail("handle changed");
    }
    if (buffer_path) {
        uint32_t length = 0;
        memcpy(&length, memory->buffer, BUFFER_LENGTH);
        if (length != 13) {
            return fail("buffer changed");
        }
    }
    if (gfx_capture_test_pre_call_rsp != gfx_capture_test_capture_rsp) {
        return fail("external RSP changed across the hook");
    }
    if (gfx_capture_test_capture.rdi != gfx_capture_test_seed.rdi ||
        gfx_capture_test_capture.rsi != gfx_capture_test_seed.rsi) {
        return fail("carrier registers changed");
    }
    return 0;
}

static void *load_code(const char *path, size_t page) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NULL;
    }
    uint8_t code[CODE_LENGTH];
    size_t got = fread(code, 1, sizeof(code), file);
    int extra = fgetc(file);
    fclose(file);
    if (got != sizeof(code) || extra != EOF) {
        return NULL;
    }
    void *mapping = mmap(NULL, page, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mapping == MAP_FAILED) {
        return NULL;
    }
    memcpy(mapping, code, sizeof(code));
    if (mprotect(mapping, page, PROT_READ | PROT_EXEC) != 0) {
        (void)munmap(mapping, page);
        return NULL;
    }
    return mapping;
}

static int run_hook(const char *subject, const char *scenario,
                    const char *code_path) {
    unsigned slot = 0;
    int buffer_path = 0;
    if (strcmp(scenario, "s03") == 0) {
        slot = 2;
        buffer_path = 1;
    } else if (strcmp(scenario, "s10") == 0) {
        slot = 3;
        buffer_path = 0;
    } else {
        return fail("unknown scenario");
    }

    int is_stub = strcmp(subject, "contract-stub") == 0;
    int is_original = strcmp(subject, "original-code-window") == 0;
    if (!is_stub && !is_original) {
        return fail("unknown subject");
    }
    if (is_original && code_path == NULL) {
        return fail("original-code-window needs a code file");
    }

    fixture_memory memory;
    if (setup_memory(&memory, buffer_path) != 0) {
        return 1;
    }

    void *code_map = NULL;
    void *target = NULL;
    if (is_stub) {
        target = (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub;
    } else {
        code_map = load_code(code_path, memory.page);
        if (code_map == NULL) {
            release_memory(&memory);
            return fail("cannot load the 75-byte code window");
        }
        target = code_map;
    }

    eu4_gfx_set_constant_buffer_capture_thread_init();

    reset_memory(&memory);
    seed_for(&memory, slot, buffer_path);
    gfx_capture_test_target = target;
    gfx_capture_test_call_target();
    gfx_capture_test_state unhooked = gfx_capture_test_capture;
    uint64_t unhooked_pre = gfx_capture_test_pre_call_rsp;
    uint64_t unhooked_post = gfx_capture_test_capture_rsp;
    memory_snapshot unhooked_memory;
    snapshot_memory(&memory, &unhooked_memory);
    if (check_post(&memory, slot, buffer_path) != 0) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return 1;
    }

    reset_memory(&memory);
    seed_for(&memory, slot, buffer_path);
    if (!eu4_gfx_set_constant_buffer_capture_install(target)) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("capture install failed");
    }
    gfx_capture_test_target = target;
    gfx_capture_test_call_target();
    if (!eu4_gfx_capture_restore()) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("capture restore failed");
    }
    if (!eu4_gfx_set_constant_buffer_capture_flush()) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("capture flush failed");
    }
    if (eu4_gfx_set_constant_buffer_capture_refusals() != 0) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("capture refused an invocation");
    }

    gfx_capture_test_state hooked = gfx_capture_test_capture;
    uint64_t hooked_pre = gfx_capture_test_pre_call_rsp;
    uint64_t hooked_post = gfx_capture_test_capture_rsp;
    memory_snapshot hooked_memory;
    snapshot_memory(&memory, &hooked_memory);
    if (check_post(&memory, slot, buffer_path) != 0) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return 1;
    }
    if (!gfx_capture_test_state_equal(&unhooked, &hooked) ||
        unhooked_pre != hooked_pre || unhooked_post != hooked_post) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("hooked and unhooked machine state differ");
    }
    if (!memory_equal(&unhooked_memory, &hooked_memory)) {
        if (code_map != NULL) {
            (void)munmap(code_map, memory.page);
        }
        release_memory(&memory);
        return fail("hooked and unhooked declared memory differ");
    }

    printf("PRB-RECEIPT subject=%s scenario=%s synthetic=1 refusals=0\n",
           subject, scenario);
    if (code_map != NULL) {
        (void)munmap(code_map, memory.page);
    }
    release_memory(&memory);
    return 0;
}

static void fill_thread_fixture(thread_fixture *fixture) {
    memset(fixture, 0, sizeof(*fixture));
    fill_ctx(fixture->ctx);
    fixture->handle = (uint64_t)(uintptr_t)fixture->ctx;
    uint32_t length = 13;
    memcpy(fixture->buffer, &length, BUFFER_LENGTH);
}

static void *tls_thread(void *argument) {
    thread_fixture *fixture = (thread_fixture *)argument;
    _Alignas(16) eu4_gfx_capture_frame frame;
    memset(&frame, 0, sizeof(frame));
    frame.rdi = (uint64_t)(uintptr_t)&fixture->handle;
    frame.rsi = (uint64_t)(uintptr_t)fixture->buffer;
    frame.rcx = 0xdeadbeef00000000ull | 3u;
    frame.r9 = 0xfeedface00000000ull | 13u;
    frame.rax = (uint64_t)(uintptr_t)fixture->ctx;
    eu4_gfx_set_constant_buffer_capture_thread_init();
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);
    if (!eu4_gfx_set_constant_buffer_capture_flush()) {
        return (void *)(uintptr_t)1;
    }
    return NULL;
}

static int run_tls(void) {
    thread_fixture fixtures[2];
    pthread_t threads[2];
    fill_thread_fixture(&fixtures[0]);
    fill_thread_fixture(&fixtures[1]);
    for (int i = 0; i < 2; i++) {
        if (pthread_create(&threads[i], NULL, tls_thread, &fixtures[i]) != 0) {
            return fail("cannot create TLS thread");
        }
    }
    for (int i = 0; i < 2; i++) {
        void *result = NULL;
        if (pthread_join(threads[i], &result) != 0) {
            return fail("cannot join TLS thread");
        }
        if (result != NULL) {
            return fail("TLS capture flush failed");
        }
    }
    printf("PRB-TLS synthetic=1\n");
    return 0;
}

static void make_direct_frame(eu4_gfx_capture_frame *frame,
                              thread_fixture *fixture) {
    memset(frame, 0, sizeof(*frame));
    frame->rdi = (uint64_t)(uintptr_t)&fixture->handle;
    frame->rsi = (uint64_t)(uintptr_t)fixture->buffer;
    frame->rcx = 0xdeadbeef00000000ull | 3u;
    frame->r9 = 0xfeedface00000000ull | 13u;
    frame->rax = (uint64_t)(uintptr_t)fixture->ctx;
}

static int run_busy(void) {
    thread_fixture accepted;
    thread_fixture refused;
    fill_thread_fixture(&accepted);
    fill_thread_fixture(&refused);

    _Alignas(16) eu4_gfx_capture_frame accepted_frame;
    _Alignas(16) eu4_gfx_capture_frame refused_frame;
    make_direct_frame(&accepted_frame, &accepted);
    make_direct_frame(&refused_frame, &refused);

    eu4_gfx_set_constant_buffer_capture_thread_init();
    /* LIFO: B's refused exit must not finalize A's active capture. */
    eu4_gfx_set_constant_buffer_capture_entry(&accepted_frame);
    eu4_gfx_set_constant_buffer_capture_entry(&refused_frame);
    eu4_gfx_set_constant_buffer_capture_exit(&refused_frame);
    eu4_gfx_set_constant_buffer_capture_exit(&accepted_frame);
    if (!eu4_gfx_set_constant_buffer_capture_flush()) {
        return fail("busy capture flush failed");
    }
    unsigned refusals = eu4_gfx_set_constant_buffer_capture_refusals();
    if (refusals != 1) {
        return fail("nested refusal was not counted once");
    }

    printf("PRB-BUSY accepted_handle=0x%llx refused_handle=0x%llx "
           "accepted_ctx=0x%llx refused_ctx=0x%llx refusals=%u\n",
           (unsigned long long)(uintptr_t)&accepted.handle,
           (unsigned long long)(uintptr_t)&refused.handle,
           (unsigned long long)(uintptr_t)accepted.ctx,
           (unsigned long long)(uintptr_t)refused.ctx, refusals);
    return 0;
}

static int run_refusal(void) {
    uint8_t ctx[0x200];
    fill_ctx(ctx);
    uint64_t null_handle = 0;
    uint64_t handle = (uint64_t)(uintptr_t)ctx;
    uint8_t buffer[BUFFER_LENGTH] = {13, 0, 0, 0};
    _Alignas(16) eu4_gfx_capture_frame frame;

    eu4_gfx_set_constant_buffer_capture_thread_init();
    memset(&frame, 0, sizeof(frame));
    frame.rdi = 0;
    frame.rsi = (uint64_t)(uintptr_t)buffer;
    frame.rcx = 0xdeadbeef00000000ull | 3u;
    frame.r9 = 0xfeedface00000000ull | 13u;
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);

    memset(&frame, 0, sizeof(frame));
    frame.rdi = (uint64_t)(uintptr_t)&null_handle;
    frame.rsi = (uint64_t)(uintptr_t)buffer;
    frame.rcx = 0xdeadbeef00000000ull | 3u;
    frame.r9 = 0xfeedface00000000ull | 13u;
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);

    memset(&frame, 0, sizeof(frame));
    frame.rdi = (uint64_t)(uintptr_t)&handle;
    frame.rsi = 0;
    frame.rcx = 0xdeadbeef00000000ull | 3u;
    frame.r9 = 0xfeedface00000000ull | 0xffffffffu;
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);

    if (eu4_gfx_set_constant_buffer_capture_flush()) {
        return fail("refused invocations produced a capture");
    }
    unsigned refusals = eu4_gfx_set_constant_buffer_capture_refusals();
    if (refusals != 3) {
        return fail("refusals were not counted");
    }
    printf("PRB-REFUSAL synthetic=1 refusals=%u flush=0\n", refusals);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s hook <subject> <scenario> [code] | "
                        "tls | busy | refusal\n",
                argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "hook") == 0) {
        if (argc < 4) {
            return fail("hook needs subject and scenario");
        }
        const char *code_path = argc >= 5 ? argv[4] : NULL;
        return run_hook(argv[2], argv[3], code_path);
    }
    if (strcmp(argv[1], "tls") == 0) {
        if (argc != 2) {
            return fail("tls takes no arguments");
        }
        return run_tls();
    }
    if (strcmp(argv[1], "busy") == 0) {
        if (argc != 2) {
            return fail("busy takes no arguments");
        }
        return run_busy();
    }
    if (strcmp(argv[1], "refusal") == 0) {
        if (argc != 2) {
            return fail("refusal takes no arguments");
        }
        return run_refusal();
    }
    return fail("unknown mode");
}
