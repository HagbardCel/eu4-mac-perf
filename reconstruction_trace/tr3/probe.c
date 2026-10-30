/* One-shot hook of tr3_subject. The first call is restored, traced, and
 * resumed. The hook is not installed again. A failed restore, VM run, or
 * resume exits nonzero. */
#include <QBDI.h>
#include <libkern/OSCacheControl.h>
#include <limits.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

#define STACK_BELOW 248
#define STACK_ABOVE 8
#define HOST_STACK (4u << 20)
#define MAX_INSTS 64
#define MAX_ACCESSES 4
#define MAX_TRANSFERS 4
#define PATCH_SIZE 5
#define DECOY 0x100000000ULL

extern const uint8_t tr3_scope_start __asm("section$start$__TEXT$__tr3_scope");
extern const uint8_t tr3_scope_end __asm("section$end$__TEXT$__tr3_scope");
uint64_t tr3_subject(uint64_t *cell);

struct tr3_region {
    char id[16];
    const char *kind;
    rword base;
    size_t size;
    uint8_t bytes[STACK_BELOW + STACK_ABOVE];
};

struct tr3_pointer {
    const char *from_register;
    int to_region;
    size_t to_offset;
};

struct tr3_inst {
    rword pc;
    rword next_pc;
    uint32_t inst_size;
    char mnemonic[32];
    char disassembly[64];
    bool may_load, may_store, is_call, is_return;
    uint32_t load_size, store_size;
    size_t n_access;
    MemoryAccess access[MAX_ACCESSES];
};

struct tr3_run {
    const char *failure;
    VMInstanceRef vm;
    bool memory_recording;
    bool ran;
    bool entered;
    GPRState entry;
    GPRState exit;
    size_t n_regions, n_pointers, n_insts, n_transfers;
    struct tr3_region regions[4];
    struct tr3_pointer pointers[4];
    struct tr3_inst insts[MAX_INSTS];
    rword transfers[MAX_TRANSFERS];
    uint64_t dropped_insts, dropped_accesses, dropped_transfers;
};

GPRState tr3_entry_gpr, tr3_exit_gpr;
FPRState tr3_entry_fpr __attribute__((aligned(16)));
FPRState tr3_exit_fpr __attribute__((aligned(16)));
uint8_t tr3_host_stack[HOST_STACK] __attribute__((aligned(16)));
extern void tr3_intercept_stub(void);

static struct tr3_run run;
static struct tr3_run *intercept_run;
static uint8_t saved_entry[PATCH_SIZE];
static int hook_count, qbdi_invocations, native_call_count;
static bool restored_before_qbdi, entry_bytes_match_after;
static uint64_t first_result, second_result;

static void copy_text(char *dst, size_t cap, const char *src) {
    size_t n = 0;
    for (; src != NULL && *src != '\0' && n + 1 < cap; src++) {
        if (*src == '\t') {
            if (n > 0) dst[n++] = ' ';
        } else if (*src != '"' && *src != '\\' && (unsigned char)*src >= 0x20) {
            dst[n++] = *src;
        }
    }
    dst[n] = '\0';
}

static void fail(const char *reason) {
    run.failure = reason;
    fprintf(stderr, "%s\n", reason);
    exit(1);
}

static bool write_entry(const uint8_t bytes[PATCH_SIZE]) {
    uintptr_t page = (uintptr_t)getpagesize();
    uintptr_t site = (uintptr_t)tr3_subject;
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

static bool install_hook(void) {
    uint8_t patch[PATCH_SIZE] = {0xe9};
    int64_t delta = (int64_t)(uintptr_t)tr3_intercept_stub -
                    (int64_t)((uintptr_t)tr3_subject + PATCH_SIZE);
    if (delta < INT32_MIN || delta > INT32_MAX) return false;
    int32_t rel = (int32_t)delta;
    memcpy(patch + 1, &rel, sizeof(rel));
    return write_entry(patch);
}

static VMAction on_entry(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)vm;
    (void)fpr;
    struct tr3_run *r = data;
    if (r->entered) return QBDI_CONTINUE;
    r->entered = true;
    r->entry = *gpr;
    for (size_t i = 0; i < r->n_regions; i++) {
        memcpy(r->regions[i].bytes, (const void *)r->regions[i].base, r->regions[i].size);
    }
    /* The caller's stack window, taken from rsp. Not derived from a loaded value. */
    struct tr3_region *s = &r->regions[r->n_regions];
    snprintf(s->id, sizeof(s->id), "stack");
    s->kind = "stack";
    s->base = gpr->rsp - STACK_BELOW;
    s->size = STACK_BELOW + STACK_ABOVE;
    memcpy(s->bytes, (const void *)s->base, s->size);
    r->pointers[r->n_pointers++] =
        (struct tr3_pointer){"rsp", (int)r->n_regions, STACK_BELOW};
    r->n_regions++;
    return QBDI_CONTINUE;
}

static VMAction on_inst(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)fpr;
    struct tr3_run *r = data;
    size_t n = 0;
    MemoryAccess *acc = qbdi_getInstMemoryAccess(vm, &n);
    if (r->n_insts == MAX_INSTS) {
        r->dropped_insts++;
        r->dropped_accesses += n;
        free(acc);
        return QBDI_CONTINUE;
    }
    const InstAnalysis *a =
        qbdi_getInstAnalysis(vm, QBDI_ANALYSIS_INSTRUCTION | QBDI_ANALYSIS_DISASSEMBLY);
    struct tr3_inst *i = &r->insts[r->n_insts++];
    i->pc = a->address;
    i->next_pc = gpr->rip;
    i->inst_size = a->instSize;
    copy_text(i->mnemonic, sizeof(i->mnemonic), a->mnemonic);
    copy_text(i->disassembly, sizeof(i->disassembly), a->disassembly);
    i->may_load = a->mayLoad;
    i->may_store = a->mayStore;
    i->is_call = a->isCall;
    i->is_return = a->isReturn;
    i->load_size = a->loadSize;
    i->store_size = a->storeSize;
    i->n_access = n < MAX_ACCESSES ? n : MAX_ACCESSES;
    if (n > MAX_ACCESSES) r->dropped_accesses += n - MAX_ACCESSES;
    if (i->n_access) memcpy(i->access, acc, i->n_access * sizeof(*acc));
    free(acc);
    return QBDI_CONTINUE;
}

static VMAction on_transfer(VMInstanceRef vm, const VMState *state, GPRState *gpr,
                            FPRState *fpr, void *data) {
    (void)vm;
    (void)state;
    (void)fpr;
    struct tr3_run *r = data;
    if (r->n_transfers == MAX_TRANSFERS) r->dropped_transfers++;
    else r->transfers[r->n_transfers++] = gpr->rip;
    return QBDI_CONTINUE;
}

static bool vm_setup(struct tr3_run *r) {
    qbdi_initVM(&r->vm, NULL, NULL, QBDI_NO_OPT);
    if (r->vm == NULL) return false;
    qbdi_addInstrumentedRange(r->vm, (rword)&tr3_scope_start, (rword)&tr3_scope_end);
    r->memory_recording = qbdi_recordMemoryAccess(r->vm, QBDI_MEMORY_READ_WRITE);
    return qbdi_addCodeAddrCB(r->vm, (rword)tr3_subject, QBDI_PREINST, on_entry, r, 0) !=
               QBDI_INVALID_EVENTID &&
           qbdi_addCodeRangeCB(r->vm, (rword)&tr3_scope_start, (rword)&tr3_scope_end,
                               QBDI_POSTINST, on_inst, r, 0) != QBDI_INVALID_EVENTID &&
           qbdi_addVMEventCB(r->vm, QBDI_EXEC_TRANSFER_CALL, on_transfer, r) !=
               QBDI_INVALID_EVENTID;
}

void tr3_intercept_dispatch(void) {
    struct tr3_run *r = intercept_run;
    hook_count++;
    if (hook_count != 1) fail("hook ran more than once");
    if (!write_entry(saved_entry)) fail("entry hook could not be removed");
    restored_before_qbdi = memcmp((const void *)tr3_subject, saved_entry, PATCH_SIZE) == 0;
    if (!restored_before_qbdi) fail("entry bytes were not restored before QBDI");
    rword stop = *(const rword *)tr3_entry_gpr.rsp;
    qbdi_setGPRState(r->vm, &tr3_entry_gpr);
    qbdi_setFPRState(r->vm, &tr3_entry_fpr);
    qbdi_invocations++;
    r->ran = qbdi_run(r->vm, (rword)tr3_subject, stop);
    tr3_exit_gpr = *qbdi_getGPRState(r->vm);
    tr3_exit_fpr = *qbdi_getFPRState(r->vm);
    r->exit = tr3_exit_gpr;
    if (!r->ran || !r->entered || tr3_exit_gpr.rip != stop ||
        tr3_exit_gpr.rsp != tr3_entry_gpr.rsp + 8) {
        fail(r->ran ? "QBDI did not return to the natural call site"
                    : "QBDI executed no block");
    }
}

static void hex_bytes(const uint8_t *p, size_t n) {
    putchar('"');
    for (size_t i = 0; i < n; i++) printf("%02x", p[i]);
    putchar('"');
}

static void emit_gpr(const GPRState *g) {
    static const char *const names[] = {"rax", "rbx", "rcx", "rdx", "rsi", "rdi",
                                        "r8",  "r9",  "r10", "r11", "r12", "r13",
                                        "r14", "r15", "rbp", "rsp", "rip", "eflags"};
    printf("{");
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        printf("%s\"%s\": \"0x%llx\"", i ? ", " : "", names[i],
               (unsigned long long)QBDI_GPR_GET(g, i));
    }
    printf("}");
}

static void emit(void) {
    uint32_t version_num = 0;
    const char *version = qbdi_getVersion(&version_num);
    int translated = -1;
    size_t len = sizeof(translated);
    if (sysctlbyname("sysctl.proc_translated", &translated, &len, NULL, 0) != 0) translated = -1;
    printf("{\"qbdi_version\": \"%s\", \"qbdi_architecture\": \"%s\", \"qbdi_platform\": "
           "\"%s\", \"proc_translated\": %d, \"decoy_address\": \"0x%llx\", ",
           version, QBDI_ARCHITECTURE_STRING, QBDI_PLATFORM_STRING, translated,
           (unsigned long long)DECOY);
    printf("\"hook\": {\"count\": %d, \"qbdi_invocations\": %d, \"native_call_count\": %d, "
           "\"restored_before_qbdi\": %s, \"entry_bytes_match_after\": %s, "
           "\"saved_entry\": ",
           hook_count, qbdi_invocations, native_call_count,
           restored_before_qbdi ? "true" : "false",
           entry_bytes_match_after ? "true" : "false");
    hex_bytes(saved_entry, PATCH_SIZE);
    printf(", \"final_entry\": ");
    hex_bytes((const uint8_t *)tr3_subject, PATCH_SIZE);
    printf(", \"first_result\": \"0x%llx\", \"second_result\": \"0x%llx\"}, ",
           (unsigned long long)first_result, (unsigned long long)second_result);
    printf("\"symbols\": {\"subject\": \"0x%llx\", \"scope_start\": \"0x%llx\", "
           "\"scope_end\": \"0x%llx\"}, \"run\": {",
           (unsigned long long)(uintptr_t)tr3_subject,
           (unsigned long long)(uintptr_t)&tr3_scope_start,
           (unsigned long long)(uintptr_t)&tr3_scope_end);
    printf("\"failure\": null, \"ran\": %s, \"entered\": %s, \"memory_recording\": %s, ",
           run.ran ? "true" : "false", run.entered ? "true" : "false",
           run.memory_recording ? "true" : "false");
    printf("\"entry_gpr\": ");
    emit_gpr(&run.entry);
    printf(", \"exit_gpr\": ");
    emit_gpr(&run.exit);
    printf(", \"regions\": [");
    for (size_t i = 0; i < run.n_regions; i++) {
        const struct tr3_region *g = &run.regions[i];
        printf("%s{\"id\": \"%s\", \"kind\": \"%s\", \"base\": \"0x%llx\", \"size\": %zu, "
               "\"bytes\": ",
               i ? ", " : "", g->id, g->kind, (unsigned long long)g->base, g->size);
        hex_bytes(g->bytes, g->size);
        printf("}");
    }
    printf("], \"pointers\": [");
    for (size_t i = 0; i < run.n_pointers; i++) {
        const struct tr3_pointer *p = &run.pointers[i];
        printf("%s{\"from\": {\"register\": \"%s\"}, \"to\": {\"region\": \"%s\", "
               "\"offset\": %zu}}",
               i ? ", " : "", p->from_register, run.regions[p->to_region].id, p->to_offset);
    }
    printf("], \"instructions\": [");
    for (size_t i = 0; i < run.n_insts; i++) {
        const struct tr3_inst *n = &run.insts[i];
        printf("%s{\"pc\": \"0x%llx\", \"next_pc\": \"0x%llx\", \"inst_size\": %u, "
               "\"mnemonic\": \"%s\", \"disassembly\": \"%s\", \"may_load\": %s, "
               "\"may_store\": %s, \"load_size\": %u, \"store_size\": %u, \"is_call\": %s, "
               "\"is_return\": %s, \"accesses\": [",
               i ? ", " : "", (unsigned long long)n->pc, (unsigned long long)n->next_pc,
               n->inst_size, n->mnemonic, n->disassembly, n->may_load ? "true" : "false",
               n->may_store ? "true" : "false", n->load_size, n->store_size,
               n->is_call ? "true" : "false", n->is_return ? "true" : "false");
        for (size_t j = 0; j < n->n_access; j++) {
            const MemoryAccess *m = &n->access[j];
            printf("%s{\"inst_address\": \"0x%llx\", \"address\": \"0x%llx\", "
                   "\"value\": \"0x%llx\", \"size\": %u, \"type\": %u, \"flags\": %u}",
                   j ? ", " : "", (unsigned long long)m->instAddress,
                   (unsigned long long)m->accessAddress, (unsigned long long)m->value,
                   m->size, (unsigned)m->type, (unsigned)m->flags);
        }
        printf("]}");
    }
    printf("], \"exec_transfers\": [");
    for (size_t i = 0; i < run.n_transfers; i++) {
        printf("%s\"0x%llx\"", i ? ", " : "", (unsigned long long)run.transfers[i]);
    }
    printf("], \"dropped_instructions\": %llu, \"dropped_accesses\": %llu, "
           "\"dropped_transfers\": %llu}}\n",
           (unsigned long long)run.dropped_insts, (unsigned long long)run.dropped_accesses,
           (unsigned long long)run.dropped_transfers);
    fflush(stdout);
}

int main(void) {
    uint64_t *cell = calloc(1, sizeof(*cell));
    if (cell == NULL) return 1;
    *cell = DECOY;
    struct tr3_region *g = &run.regions[run.n_regions];
    snprintf(g->id, sizeof(g->id), "cell");
    g->kind = "heap";
    g->base = (rword)cell;
    g->size = sizeof(*cell);
    run.pointers[run.n_pointers++] = (struct tr3_pointer){"rdi", (int)run.n_regions, 0};
    run.n_regions++;
    intercept_run = &run;
    if (!vm_setup(&run)) {
        fprintf(stderr, "QBDI VM setup failed\n");
        return 1;
    }
    memcpy(saved_entry, (const void *)tr3_subject, PATCH_SIZE);
    if (!install_hook()) {
        fprintf(stderr, "entry hook could not be installed\n");
        return 1;
    }
    native_call_count++;
    first_result = tr3_subject(cell);
    native_call_count++;
    second_result = tr3_subject(cell);
    entry_bytes_match_after = memcmp((const void *)tr3_subject, saved_entry, PATCH_SIZE) == 0;
    qbdi_terminateVM(run.vm);
    emit();
    if (hook_count != 1 || qbdi_invocations != 1 || native_call_count != 2 ||
        !restored_before_qbdi || !entry_bytes_match_after || first_result != DECOY + 1 ||
        second_result != DECOY + 2 || *cell != DECOY + 2) {
        fprintf(stderr, "one-shot subject did not match the native result\n");
        return 1;
    }
    return 0;
}
