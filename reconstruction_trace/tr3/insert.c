/* Startup dylib. The constructor may install one jmp rel32 into the host's
 * tr3_subject. The first call restores those bytes, traces them, and does not
 * reinstall the hook. A jump that does not fit, or a byte that does not match,
 * leaves the host untouched. This dylib does not run EU4. */
#include <CommonCrypto/CommonDigest.h>
#include <QBDI.h>
#include <dlfcn.h>
#include <errno.h>
#include <libkern/OSCacheControl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/machine.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

#define STACK_BELOW 248
#define STACK_ABOVE 8
#define HOST_STACK (4u << 20)
#define PATCH_SIZE 5
#define MAX_INSTS 16
#define MAX_ACCESSES 4
#define MAX_TRANSFERS 4

extern void tr3_insert_stub(void);
GPRState tr3_entry_gpr, tr3_exit_gpr;
FPRState tr3_entry_fpr __attribute__((aligned(16)));
FPRState tr3_exit_fpr __attribute__((aligned(16)));
uint8_t tr3_insert_stack[HOST_STACK] __attribute__((aligned(16)));
uint64_t tr3_subject_site;

struct region {
    char id[16];
    const char *kind;
    rword base;
    size_t size;
    uint8_t bytes[STACK_BELOW + STACK_ABOVE];
};

struct pointer {
    const char *reg;
    int to_region;
    size_t to_offset;
};

struct inst {
    rword pc, next_pc;
    uint32_t inst_size, load_size, store_size;
    char mnemonic[32], disassembly[64];
    bool may_load, may_store, is_call, is_return;
    size_t n_access;
    MemoryAccess access[MAX_ACCESSES];
};

struct run {
    const char *failure;
    VMInstanceRef vm;
    bool memory_recording, ran, entered;
    GPRState entry, exit;
    size_t n_regions, n_pointers, n_insts, n_transfers;
    struct region regions[4];
    struct pointer pointers[4];
    struct inst insts[MAX_INSTS];
    rword transfers[MAX_TRANSFERS];
    uint64_t dropped_insts, dropped_accesses, dropped_transfers;
};

static struct run run;
static uint8_t saved_entry[PATCH_SIZE];
static uint8_t window_bytes[16];
static size_t window_len;
static const char *refusal;
static const char *image_name;
static const char *expect_file;
static const char *expect_window;
static char file_sha[65];
static char window_sha[65];
static uint64_t preferred_base, entry_rva;
static intptr_t image_slide;
static uintptr_t image_header;
static int hook_count, qbdi_invocations;
static bool patched, restored_before_qbdi, entry_bytes_match_after, vm_ready;
static bool have_saved;

static void copy_text(char *dst, size_t cap, const char *src) {
    size_t n = 0;
    for (; src != NULL && *src && n + 1 < cap; src++) {
        unsigned char c = (unsigned char)*src;
        if (c == '\t') {
            if (n > 0) dst[n++] = ' ';
        } else if (c >= 0x20 && c != '"' && c != '\\') {
            dst[n++] = (char)c;
        }
    }
    dst[n] = '\0';
}

static bool parse_u64(const char *text, uint64_t *out) {
    if (text == NULL || *text == '\0') return false;
    errno = 0;
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0') return false;
    *out = value;
    return true;
}

static void sha256_hex(const uint8_t *data, size_t n, char out[65]) {
    unsigned char dig[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data, (CC_LONG)n, dig);
    for (size_t i = 0; i < sizeof(dig); i++) sprintf(out + 2 * i, "%02x", dig[i]);
    out[64] = '\0';
}

static bool sha256_path(const char *path, char out[65]) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) return false;
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    uint8_t buf[1 << 16];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), file)) > 0) CC_SHA256_Update(&ctx, buf, (CC_LONG)n);
    int bad = ferror(file);
    fclose(file);
    if (bad) return false;
    unsigned char dig[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(dig, &ctx);
    for (size_t i = 0; i < sizeof(dig); i++) sprintf(out + 2 * i, "%02x", dig[i]);
    out[64] = '\0';
    return true;
}

static bool text_base(const struct mach_header_64 *header, uint64_t *out) {
    const uint8_t *cursor = (const uint8_t *)header + sizeof(*header);
    for (uint32_t i = 0; i < header->ncmds; i++) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmdsize < sizeof(*command)) return false;
        if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const struct segment_command_64 *)cursor;
            if (strncmp(segment->segname, "__TEXT", sizeof(segment->segname)) == 0) {
                *out = segment->vmaddr;
                return true;
            }
        }
        cursor += command->cmdsize;
    }
    return false;
}

static bool file_offset(const struct mach_header_64 *header, uint64_t va, size_t n,
                        uint64_t *offset) {
    const uint8_t *cursor = (const uint8_t *)header + sizeof(*header);
    for (uint32_t i = 0; i < header->ncmds; i++) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmdsize < sizeof(*command)) return false;
        if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const struct segment_command_64 *)cursor;
            if (segment->vmaddr <= va && va + n <= segment->vmaddr + segment->filesize) {
                *offset = segment->fileoff + (va - segment->vmaddr);
                return true;
            }
        }
        cursor += command->cmdsize;
    }
    return false;
}

static bool read_file_window(uint64_t va) {
    uint64_t offset = 0;
    if (!file_offset((const struct mach_header_64 *)image_header, va, window_len, &offset)) {
        return false;
    }
    FILE *file = fopen(image_name, "rb");
    if (file == NULL) return false;
    bool ok = fseeko(file, (off_t)offset, SEEK_SET) == 0 &&
              fread(window_bytes, 1, window_len, file) == window_len;
    fclose(file);
    return ok;
}

static bool expect_hex(const char *got, const char *expected) {
    return expected != NULL && strlen(expected) == 64 && strcmp(got, expected) == 0;
}

static bool write_entry(const uint8_t bytes[PATCH_SIZE]) {
    uintptr_t page = (uintptr_t)getpagesize();
    uintptr_t site = (uintptr_t)tr3_subject_site;
    uintptr_t begin = site & ~(page - 1);
    mach_vm_size_t len = ((site + PATCH_SIZE + page - 1) & ~(page - 1)) - begin;
    if (mach_vm_protect(mach_task_self(), begin, len, false,
                        VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY) !=
        KERN_SUCCESS) {
        return false;
    }
    memcpy((void *)site, bytes, PATCH_SIZE);
    sys_icache_invalidate((void *)site, PATCH_SIZE);
    return mach_vm_protect(mach_task_self(), begin, len, false,
                           VM_PROT_READ | VM_PROT_EXECUTE) == KERN_SUCCESS;
}

static void refuse(const char *reason) { refusal = reason; }

static void authenticate(void) {
    const struct mach_header_64 *main_image = NULL;
    const char *name = NULL;
    intptr_t slide = 0;
    uint32_t matches = 0;
    uint32_t images = _dyld_image_count();
    for (uint32_t i = 0; i < images; i++) {
        const struct mach_header *raw = _dyld_get_image_header(i);
        if (raw == NULL || raw->magic != MH_MAGIC_64) continue;
        const struct mach_header_64 *header = (const struct mach_header_64 *)raw;
        if (header->filetype != MH_EXECUTE || header->cputype != CPU_TYPE_X86_64) continue;
        matches++;
        main_image = header;
        name = _dyld_get_image_name(i);
        slide = _dyld_get_image_vmaddr_slide(i);
    }
    if (matches != 1 || main_image == NULL || name == NULL) {
        refuse("main x86-64 MH_EXECUTE was not singular");
        return;
    }
    image_header = (uintptr_t)main_image;
    image_slide = slide;
    image_name = name;
    if (!text_base(main_image, &preferred_base)) {
        refuse("main image has no __TEXT");
        return;
    }
    if (image_header != (uintptr_t)((intptr_t)preferred_base + slide)) {
        refuse("loaded header is not the preferred base plus the slide");
        return;
    }
    uint64_t supplied_len = 0;
    if (!parse_u64(getenv("TR3_ENTRY_RVA"), &entry_rva) ||
        !parse_u64(getenv("TR3_WINDOW_LEN"), &supplied_len) || supplied_len == 0 ||
        supplied_len > sizeof(window_bytes)) {
        refuse("entry window was not supplied");
        return;
    }
    window_len = (size_t)supplied_len;
    expect_file = getenv("TR3_EXPECT_FILE_SHA256");
    expect_window = getenv("TR3_EXPECT_WINDOW_SHA256");
    if (expect_file == NULL || expect_window == NULL) {
        refuse("expected digest was not supplied");
        return;
    }
    tr3_subject_site = (uint64_t)((intptr_t)preferred_base + slide + (intptr_t)entry_rva);
    void *symbol = dlsym(RTLD_MAIN_ONLY, "tr3_subject");
    if (symbol == NULL || (uintptr_t)symbol != (uintptr_t)tr3_subject_site) {
        refuse("subject symbol is not the slid entry");
        return;
    }
    if (!sha256_path(name, file_sha) || !expect_hex(file_sha, expect_file)) {
        refuse("file hash mismatch");
        return;
    }
    uint64_t preferred_site = preferred_base + entry_rva;
    if (!read_file_window(preferred_site)) {
        refuse("code window is not file-backed");
        return;
    }
    sha256_hex(window_bytes, window_len, window_sha);
    if (!expect_hex(window_sha, expect_window)) {
        refuse("window hash mismatch");
        return;
    }
    if (memcmp((const void *)tr3_subject_site, window_bytes, window_len) != 0) {
        refuse("mapped bytes mismatch");
        return;
    }
    memcpy(saved_entry, window_bytes, PATCH_SIZE);
    have_saved = true;
    int64_t delta = (int64_t)(uintptr_t)tr3_insert_stub - (int64_t)(tr3_subject_site + PATCH_SIZE);
    if (delta < INT32_MIN || delta > INT32_MAX) {
        refuse("rel32 out of range");
        return;
    }
    uint8_t patch[PATCH_SIZE] = {0xe9};
    int32_t rel = (int32_t)delta;
    memcpy(patch + 1, &rel, sizeof(rel));
    if (!write_entry(patch)) {
        write_entry(saved_entry);
        refuse("entry hook could not be installed");
        return;
    }
    patched = true;
}

static VMAction on_entry(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)vm;
    (void)fpr;
    struct run *current = data;
    if (current->entered) return QBDI_CONTINUE;
    current->entered = true;
    current->entry = *gpr;
    struct region *cell = &current->regions[current->n_regions];
    snprintf(cell->id, sizeof(cell->id), "cell");
    cell->kind = "heap";
    cell->base = gpr->rdi;
    cell->size = 8;
    memcpy(cell->bytes, (const void *)gpr->rdi, cell->size);
    current->pointers[current->n_pointers++] =
        (struct pointer){"rdi", (int)current->n_regions, 0};
    current->n_regions++;
    struct region *stack = &current->regions[current->n_regions];
    snprintf(stack->id, sizeof(stack->id), "stack");
    stack->kind = "stack";
    stack->base = gpr->rsp - STACK_BELOW;
    stack->size = STACK_BELOW + STACK_ABOVE;
    memcpy(stack->bytes, (const void *)stack->base, stack->size);
    current->pointers[current->n_pointers++] =
        (struct pointer){"rsp", (int)current->n_regions, STACK_BELOW};
    current->n_regions++;
    return QBDI_CONTINUE;
}

static VMAction on_inst(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)fpr;
    struct run *current = data;
    size_t count = 0;
    MemoryAccess *access = qbdi_getInstMemoryAccess(vm, &count);
    if (current->n_insts == MAX_INSTS) {
        current->dropped_insts++;
        current->dropped_accesses += count;
        free(access);
        return QBDI_CONTINUE;
    }
    const InstAnalysis *analysis =
        qbdi_getInstAnalysis(vm, QBDI_ANALYSIS_INSTRUCTION | QBDI_ANALYSIS_DISASSEMBLY);
    struct inst *item = &current->insts[current->n_insts++];
    item->pc = analysis->address;
    item->next_pc = gpr->rip;
    item->inst_size = analysis->instSize;
    copy_text(item->mnemonic, sizeof(item->mnemonic), analysis->mnemonic);
    copy_text(item->disassembly, sizeof(item->disassembly), analysis->disassembly);
    item->may_load = analysis->mayLoad;
    item->may_store = analysis->mayStore;
    item->is_call = analysis->isCall;
    item->is_return = analysis->isReturn;
    item->load_size = analysis->loadSize;
    item->store_size = analysis->storeSize;
    item->n_access = count < MAX_ACCESSES ? count : MAX_ACCESSES;
    if (count > MAX_ACCESSES) current->dropped_accesses += count - MAX_ACCESSES;
    if (item->n_access) memcpy(item->access, access, item->n_access * sizeof(*access));
    free(access);
    return QBDI_CONTINUE;
}

static VMAction on_transfer(VMInstanceRef vm, const VMState *state, GPRState *gpr, FPRState *fpr,
                            void *data) {
    (void)vm;
    (void)state;
    (void)fpr;
    struct run *current = data;
    if (current->n_transfers == MAX_TRANSFERS) current->dropped_transfers++;
    else current->transfers[current->n_transfers++] = gpr->rip;
    return QBDI_CONTINUE;
}

static bool vm_setup(void) {
    qbdi_initVM(&run.vm, NULL, NULL, QBDI_NO_OPT);
    if (run.vm == NULL) return false;
    rword begin = (rword)tr3_subject_site;
    rword end = begin + (rword)window_len;
    qbdi_addInstrumentedRange(run.vm, begin, end);
    run.memory_recording = qbdi_recordMemoryAccess(run.vm, QBDI_MEMORY_READ_WRITE);
    vm_ready = qbdi_addCodeAddrCB(run.vm, begin, QBDI_PREINST, on_entry, &run, 0) !=
                   QBDI_INVALID_EVENTID &&
               qbdi_addCodeRangeCB(run.vm, begin, end, QBDI_POSTINST, on_inst, &run, 0) !=
                   QBDI_INVALID_EVENTID &&
               qbdi_addVMEventCB(run.vm, QBDI_EXEC_TRANSFER_CALL, on_transfer, &run) !=
                   QBDI_INVALID_EVENTID;
    return vm_ready;
}

static void hex_bytes(FILE *out, const uint8_t *bytes, size_t n) {
    fputc('"', out);
    for (size_t i = 0; i < n; i++) fprintf(out, "%02x", bytes[i]);
    fputc('"', out);
}

static void emit_gpr(FILE *out, const GPRState *gpr) {
    static const char *const names[] = {"rax", "rbx", "rcx", "rdx", "rsi", "rdi", "r8",  "r9",
                                        "r10", "r11", "r12", "r13", "r14", "r15", "rbp", "rsp",
                                        "rip", "eflags"};
    fputc('{', out);
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        fprintf(out, "%s\"%s\": \"0x%llx\"", i ? ", " : "", names[i],
                (unsigned long long)QBDI_GPR_GET(gpr, i));
    }
    fputc('}', out);
}

static void write_log(void) {
    const char *path = getenv("TR3_LOG");
    FILE *out = path ? fopen(path, "w") : stderr;
    if (out == NULL) return;
    int translated = -1;
    size_t len = sizeof(translated);
    if (sysctlbyname("sysctl.proc_translated", &translated, &len, NULL, 0) != 0) translated = -1;
    uint32_t version_num = 0;
    const char *version = qbdi_getVersion(&version_num);
    fprintf(out,
            "{\"refusal\": %s%s%s, \"patched\": %s, \"proc_translated\": %d, "
            "\"qbdi_version\": \"%s\", \"expected_file_sha256\": \"%s\", "
            "\"file_sha256\": \"%s\", \"expected_window_sha256\": \"%s\", "
            "\"window_sha256\": \"%s\", ",
            refusal ? "\"" : "", refusal ? refusal : "null", refusal ? "\"" : "",
            patched ? "true" : "false", translated, version ? version : "",
            expect_file ? expect_file : "", file_sha, expect_window ? expect_window : "",
            window_sha);
    fprintf(out,
            "\"image\": {\"preferred_base\": \"0x%llx\", \"slide\": \"0x%llx\", "
            "\"header\": \"0x%llx\", \"entry_rva\": \"0x%llx\", \"runtime_site\": \"0x%llx\", "
            "\"filetype\": \"MH_EXECUTE\", \"cputype\": \"CPU_TYPE_X86_64\"}, ",
            (unsigned long long)preferred_base, (unsigned long long)image_slide,
            (unsigned long long)image_header, (unsigned long long)entry_rva,
            (unsigned long long)tr3_subject_site);
    fprintf(out,
            "\"hook\": {\"count\": %d, \"qbdi_invocations\": %d, \"restored_before_qbdi\": %s, "
            "\"entry_bytes_match_after\": %s, \"saved_entry\": ",
            hook_count, qbdi_invocations, restored_before_qbdi ? "true" : "false",
            entry_bytes_match_after ? "true" : "false");
    hex_bytes(out, have_saved ? saved_entry : (const uint8_t *)"", have_saved ? PATCH_SIZE : 0);
    fprintf(out,
            "}, \"symbols\": {\"subject\": \"0x%llx\", \"scope_start\": \"0x%llx\", "
            "\"scope_end\": \"0x%llx\"}, \"run\": {",
            (unsigned long long)tr3_subject_site, (unsigned long long)tr3_subject_site,
            (unsigned long long)(tr3_subject_site + window_len));
    fprintf(out, "\"failure\": %s%s%s, \"ran\": %s, \"entered\": %s, \"memory_recording\": %s, ",
            run.failure ? "\"" : "", run.failure ? run.failure : "null", run.failure ? "\"" : "",
            run.ran ? "true" : "false", run.entered ? "true" : "false",
            run.memory_recording ? "true" : "false");
    fprintf(out, "\"entry_gpr\": ");
    emit_gpr(out, &run.entry);
    fprintf(out, ", \"exit_gpr\": ");
    emit_gpr(out, &run.exit);
    fprintf(out, ", \"regions\": [");
    for (size_t i = 0; i < run.n_regions; i++) {
        const struct region *region = &run.regions[i];
        fprintf(out,
                "%s{\"id\": \"%s\", \"kind\": \"%s\", \"base\": \"0x%llx\", \"size\": %zu, "
                "\"bytes\": ",
                i ? ", " : "", region->id, region->kind, (unsigned long long)region->base,
                region->size);
        hex_bytes(out, region->bytes, region->size);
        fputc('}', out);
    }
    fprintf(out, "], \"pointers\": [");
    for (size_t i = 0; i < run.n_pointers; i++) {
        const struct pointer *edge = &run.pointers[i];
        fprintf(out,
                "%s{\"from\": {\"register\": \"%s\"}, \"to\": {\"region\": \"%s\", "
                "\"offset\": %zu}}",
                i ? ", " : "", edge->reg, run.regions[edge->to_region].id, edge->to_offset);
    }
    fprintf(out, "], \"instructions\": [");
    for (size_t i = 0; i < run.n_insts; i++) {
        const struct inst *item = &run.insts[i];
        fprintf(out,
                "%s{\"pc\": \"0x%llx\", \"next_pc\": \"0x%llx\", \"inst_size\": %u, "
                "\"mnemonic\": \"%s\", \"disassembly\": \"%s\", \"may_load\": %s, "
                "\"may_store\": %s, \"load_size\": %u, \"store_size\": %u, \"is_call\": %s, "
                "\"is_return\": %s, \"accesses\": [",
                i ? ", " : "", (unsigned long long)item->pc, (unsigned long long)item->next_pc,
                item->inst_size, item->mnemonic, item->disassembly, item->may_load ? "true" : "false",
                item->may_store ? "true" : "false", item->load_size, item->store_size,
                item->is_call ? "true" : "false", item->is_return ? "true" : "false");
        for (size_t j = 0; j < item->n_access; j++) {
            const MemoryAccess *access = &item->access[j];
            fprintf(out,
                    "%s{\"inst_address\": \"0x%llx\", \"address\": \"0x%llx\", "
                    "\"value\": \"0x%llx\", \"size\": %u, \"type\": %u, \"flags\": %u}",
                    j ? ", " : "", (unsigned long long)access->instAddress,
                    (unsigned long long)access->accessAddress, (unsigned long long)access->value,
                    access->size, (unsigned)access->type, (unsigned)access->flags);
        }
        fprintf(out, "]}");
    }
    fprintf(out, "], \"exec_transfers\": [");
    for (size_t i = 0; i < run.n_transfers; i++) {
        fprintf(out, "%s\"0x%llx\"", i ? ", " : "", (unsigned long long)run.transfers[i]);
    }
    fprintf(out,
            "], \"dropped_instructions\": %llu, \"dropped_accesses\": %llu, "
            "\"dropped_transfers\": %llu}}\n",
            (unsigned long long)run.dropped_insts, (unsigned long long)run.dropped_accesses,
            (unsigned long long)run.dropped_transfers);
    if (out != stderr) fclose(out);
}

static void fail(const char *reason) {
    refusal = reason;
    exit(1);
}

void tr3_insert_dispatch(void) {
    hook_count++;
    if (hook_count != 1) fail("hook ran more than once");
    if (!write_entry(saved_entry)) fail("entry hook could not be removed");
    restored_before_qbdi = memcmp((const void *)tr3_subject_site, saved_entry, PATCH_SIZE) == 0;
    if (!restored_before_qbdi) fail("entry bytes were not restored before QBDI");
    if (!vm_setup()) fail("QBDI VM setup failed");
    rword stop = *(const rword *)tr3_entry_gpr.rsp;
    qbdi_setGPRState(run.vm, &tr3_entry_gpr);
    qbdi_setFPRState(run.vm, &tr3_entry_fpr);
    qbdi_invocations++;
    run.ran = qbdi_run(run.vm, (rword)tr3_subject_site, stop);
    tr3_exit_gpr = *qbdi_getGPRState(run.vm);
    tr3_exit_fpr = *qbdi_getFPRState(run.vm);
    run.exit = tr3_exit_gpr;
    if (!run.ran || !run.entered || tr3_exit_gpr.rip != stop ||
        tr3_exit_gpr.rsp != tr3_entry_gpr.rsp + 8) {
        fail(run.ran ? "QBDI did not return to the natural call site" : "QBDI executed no block");
    }
}

__attribute__((constructor)) static void tr3_insert_init(void) { authenticate(); }

__attribute__((destructor)) static void tr3_insert_fini(void) {
    if (have_saved) {
        entry_bytes_match_after =
            memcmp((const void *)(uintptr_t)tr3_subject_site, saved_entry, PATCH_SIZE) == 0;
    }
    if (run.vm != NULL) qbdi_terminateVM(run.vm);
    write_log();
}
