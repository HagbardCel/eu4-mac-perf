/* TR0: QBDI under Rosetta. Runs tr0_chase natively, under qbdi_call, and via a
 * natural call intercepted into QBDI, then prints the raw observation as JSON.
 * Usage: probe <head value> <tail value> */
#include <QBDI.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#include "probe_subject.h"

extern const uint8_t tr0_scope_start __asm("section$start$__TEXT$__tr0_scope");
extern const uint8_t tr0_scope_end __asm("section$end$__TEXT$__tr0_scope");

/* Generic stack window around the entry stack pointer. */
#define STACK_BELOW 248
#define STACK_ABOVE 8
#define VIRTUAL_STACK (1u << 20)
#define HOST_STACK (4u << 20)
#define MAX_REGIONS 8
#define MAX_POINTERS 8
#define MAX_INSTS 256
#define MAX_ACCESSES 8
#define MAX_TRANSFERS 8
#define PATCH_SIZE 5

struct tr0_region {
    char id[16];
    const char *kind;
    rword base;
    size_t size;
    uint8_t bytes[STACK_BELOW + STACK_ABOVE];
};

struct tr0_pointer {
    const char *from_register; /* NULL when the source is a region offset */
    int from_region;
    size_t from_offset;
    int to_region;
    size_t to_offset;
};

struct tr0_inst {
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

struct tr0_run {
    const char *mode;
    const char *failure;
    VMInstanceRef vm;
    bool memory_recording;
    bool ran;
    bool entered;
    GPRState entry;
    GPRState exit;
    rword result;
    int64_t out_value;
    uint64_t wall_ns;
    size_t n_regions, n_pointers, n_insts, n_transfers;
    struct tr0_region regions[MAX_REGIONS];
    struct tr0_pointer pointers[MAX_POINTERS];
    struct tr0_inst insts[MAX_INSTS];
    rword transfers[MAX_TRANSFERS];
    uint64_t dropped_insts, dropped_accesses, dropped_transfers;
};

static struct tr0_run runs[2];
static int64_t head_value, tail_value;
static int64_t native_result, native_out;
static uint64_t native_ns;
static bool hook_installed, hook_removed;

/* Shared with tr0_intercept_stub.S. */
GPRState tr0_entry_gpr, tr0_exit_gpr;
FPRState tr0_entry_fpr, tr0_exit_fpr;
uint8_t tr0_host_stack[HOST_STACK] __attribute__((aligned(16)));
extern void tr0_intercept_stub(void);
static struct tr0_run *intercept_run;
static uint8_t saved_entry[PATCH_SIZE];

static uint64_t now_ns(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }

/* JSON-safe copy of QBDI text: tabs become spaces, quotes and controls drop. */
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

/* ---- generic harness inventory ------------------------------------------ */

static int inv_alloc(struct tr0_run *r, size_t size, void **out) {
    struct tr0_region *g = &r->regions[r->n_regions];
    void *p = calloc(1, size);
    if (p == NULL) abort();
    snprintf(g->id, sizeof(g->id), "heap%zu", r->n_regions);
    g->kind = "heap";
    g->base = (rword)p;
    g->size = size;
    *out = p;
    return (int)r->n_regions++;
}

static void inv_store_pointer(struct tr0_run *r, int from, size_t from_offset, int to,
                              size_t to_offset) {
    rword target = r->regions[to].base + to_offset;
    memcpy((void *)(r->regions[from].base + from_offset), &target, sizeof(target));
    r->pointers[r->n_pointers++] = (struct tr0_pointer){NULL, from, from_offset, to, to_offset};
}

static void inv_register_pointer(struct tr0_run *r, const char *reg, int to, size_t to_offset) {
    r->pointers[r->n_pointers++] = (struct tr0_pointer){reg, -1, 0, to, to_offset};
}

static void build_inventory(struct tr0_run *r, struct tr0_node **head, int64_t **out) {
    void *a, *b, *c, *o;
    int ia = inv_alloc(r, sizeof(struct tr0_node), &a);
    int ib = inv_alloc(r, sizeof(struct tr0_node), &b);
    int ic = inv_alloc(r, sizeof(struct tr0_node), &c);
    int io = inv_alloc(r, sizeof(int64_t), &o);
    inv_store_pointer(r, ia, offsetof(struct tr0_node, next), ib, 0);
    inv_store_pointer(r, ib, offsetof(struct tr0_node, next), ic, 0);
    ((struct tr0_node *)a)->value = head_value;
    ((struct tr0_node *)b)->value = 0;
    ((struct tr0_node *)c)->value = tail_value;
    inv_register_pointer(r, "rdi", ia, 0);
    inv_register_pointer(r, "rsi", io, 0);
    *head = a;
    *out = o;
}

/* ---- QBDI callbacks -------------------------------------------------------- */

static VMAction on_entry(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)vm;
    (void)fpr;
    struct tr0_run *r = data;
    if (r->entered) return QBDI_CONTINUE;
    r->entered = true;
    r->entry = *gpr;
    for (size_t i = 0; i < r->n_regions; i++) {
        memcpy(r->regions[i].bytes, (const void *)r->regions[i].base, r->regions[i].size);
    }
    struct tr0_region *s = &r->regions[r->n_regions];
    snprintf(s->id, sizeof(s->id), "stack");
    s->kind = "stack";
    s->base = gpr->rsp - STACK_BELOW;
    s->size = STACK_BELOW + STACK_ABOVE;
    memcpy(s->bytes, (const void *)s->base, s->size);
    inv_register_pointer(r, "rsp", (int)r->n_regions++, STACK_BELOW);
    return QBDI_CONTINUE;
}

static VMAction on_inst(VMInstanceRef vm, GPRState *gpr, FPRState *fpr, void *data) {
    (void)fpr;
    struct tr0_run *r = data;
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
    struct tr0_inst *i = &r->insts[r->n_insts++];
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
    struct tr0_run *r = data;
    if (r->n_transfers == MAX_TRANSFERS) {
        r->dropped_transfers++;
    } else {
        r->transfers[r->n_transfers++] = gpr->rip;
    }
    return QBDI_CONTINUE;
}

static bool vm_setup(struct tr0_run *r) {
    qbdi_initVM(&r->vm, NULL, NULL, QBDI_NO_OPT);
    if (r->vm == NULL) {
        r->failure = "qbdi_initVM returned no VM";
        return false;
    }
    qbdi_addInstrumentedRange(r->vm, (rword)&tr0_scope_start, (rword)&tr0_scope_end);
    r->memory_recording = qbdi_recordMemoryAccess(r->vm, QBDI_MEMORY_READ_WRITE);
    if (qbdi_addCodeAddrCB(r->vm, (rword)tr0_chase, QBDI_PREINST, on_entry, r, 0) ==
            QBDI_INVALID_EVENTID ||
        qbdi_addCodeRangeCB(r->vm, (rword)&tr0_scope_start, (rword)&tr0_scope_end,
                            QBDI_POSTINST, on_inst, r, 0) == QBDI_INVALID_EVENTID ||
        qbdi_addVMEventCB(r->vm, QBDI_EXEC_TRANSFER_CALL, on_transfer, r) ==
            QBDI_INVALID_EVENTID) {
        r->failure = "QBDI callback registration failed";
        return false;
    }
    return true;
}

/* ---- integration (a): QBDI calls the function ----------------------------- */

static void run_vm_call(struct tr0_run *r) {
    struct tr0_node *head;
    int64_t *out;
    r->mode = "vm_call";
    build_inventory(r, &head, &out);
    if (!vm_setup(r)) return;
    uint8_t *stack = NULL;
    if (!qbdi_allocateVirtualStack(qbdi_getGPRState(r->vm), VIRTUAL_STACK, &stack)) {
        r->failure = "qbdi_allocateVirtualStack failed";
        return;
    }
    uint64_t t0 = now_ns();
    r->ran = qbdi_call(r->vm, &r->result, (rword)tr0_chase, 2, (rword)head, (rword)out);
    r->wall_ns = now_ns() - t0;
    r->exit = *qbdi_getGPRState(r->vm);
    r->out_value = *out;
    if (!r->ran) r->failure = "qbdi_call executed no block";
    qbdi_alignedFree(stack);
    qbdi_terminateVM(r->vm);
}

/* ---- integration (b): a natural call intercepted into QBDI ---------------- */

static bool write_entry(const uint8_t bytes[PATCH_SIZE]) {
    uintptr_t page = (uintptr_t)getpagesize();
    uintptr_t site = (uintptr_t)tr0_chase;
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
    int64_t delta = (int64_t)(uintptr_t)tr0_intercept_stub -
                    (int64_t)((uintptr_t)tr0_chase + PATCH_SIZE);
    if (delta < INT32_MIN || delta > INT32_MAX) return false;
    int32_t rel = (int32_t)delta;
    memcpy(patch + 1, &rel, sizeof(rel));
    return write_entry(patch);
}

static void emit(void);

/* Runs on tr0_host_stack; the guest keeps the natural caller's stack. */
void tr0_intercept_dispatch(void) {
    struct tr0_run *r = intercept_run;
    hook_removed = write_entry(saved_entry);
    rword stop = *(const rword *)tr0_entry_gpr.rsp;
    qbdi_setGPRState(r->vm, &tr0_entry_gpr);
    qbdi_setFPRState(r->vm, &tr0_entry_fpr);
    r->ran = hook_removed && qbdi_run(r->vm, (rword)tr0_chase, stop);
    tr0_exit_gpr = *qbdi_getGPRState(r->vm);
    tr0_exit_fpr = *qbdi_getFPRState(r->vm);
    r->exit = tr0_exit_gpr;
    if (!r->ran || tr0_exit_gpr.rip != stop || tr0_exit_gpr.rsp != tr0_entry_gpr.rsp + 8) {
        /* The natural caller cannot be resumed from this state. */
        r->failure = hook_removed ? "QBDI did not return to the natural call site"
                                  : "entry hook could not be removed";
        emit();
        exit(0);
    }
    hook_installed = install_hook();
}

static void run_interception(struct tr0_run *r) {
    struct tr0_node *head;
    int64_t *out;
    r->mode = "interception";
    build_inventory(r, &head, &out);
    if (!vm_setup(r)) return;
    intercept_run = r;
    memcpy(saved_entry, (const void *)tr0_chase, PATCH_SIZE);
    hook_installed = install_hook();
    if (!hook_installed) {
        r->failure = "entry hook could not be installed";
        return;
    }
    uint64_t t0 = now_ns();
    r->result = (rword)tr0_chase(head, out);
    r->wall_ns = now_ns() - t0;
    r->out_value = *out;
    hook_removed = write_entry(saved_entry);
    hook_installed = false;
    qbdi_terminateVM(r->vm);
}

/* ---- raw JSON ---------------------------------------------------------------- */

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

static void emit_run(const struct tr0_run *r) {
    printf("{\"mode\": \"%s\", \"ran\": %s, \"entered\": %s, \"memory_recording\": %s, ",
           r->mode ? r->mode : "", r->ran ? "true" : "false", r->entered ? "true" : "false",
           r->memory_recording ? "true" : "false");
    if (r->failure) {
        printf("\"failure\": \"%s\", ", r->failure);
    } else {
        printf("\"failure\": null, ");
    }
    printf("\"result\": \"0x%llx\", \"out_value\": %lld, \"wall_ns\": %llu, ",
           (unsigned long long)r->result, (long long)r->out_value,
           (unsigned long long)r->wall_ns);
    printf("\"entry_gpr\": ");
    emit_gpr(&r->entry);
    printf(", \"exit_gpr\": ");
    emit_gpr(&r->exit);
    printf(", \"regions\": [");
    for (size_t i = 0; i < r->n_regions; i++) {
        const struct tr0_region *g = &r->regions[i];
        printf("%s{\"id\": \"%s\", \"kind\": \"%s\", \"base\": \"0x%llx\", \"size\": %zu, "
               "\"bytes\": ",
               i ? ", " : "", g->id, g->kind, (unsigned long long)g->base, g->size);
        hex_bytes(g->bytes, g->size);
        printf("}");
    }
    printf("], \"pointers\": [");
    for (size_t i = 0; i < r->n_pointers; i++) {
        const struct tr0_pointer *p = &r->pointers[i];
        printf("%s{\"from\": ", i ? ", " : "");
        if (p->from_register) {
            printf("{\"register\": \"%s\"}", p->from_register);
        } else {
            printf("{\"region\": \"%s\", \"offset\": %zu}", r->regions[p->from_region].id,
                   p->from_offset);
        }
        printf(", \"to\": {\"region\": \"%s\", \"offset\": %zu}}", r->regions[p->to_region].id,
               p->to_offset);
    }
    printf("], \"instructions\": [");
    for (size_t i = 0; i < r->n_insts; i++) {
        const struct tr0_inst *n = &r->insts[i];
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
    for (size_t i = 0; i < r->n_transfers; i++) {
        printf("%s\"0x%llx\"", i ? ", " : "", (unsigned long long)r->transfers[i]);
    }
    printf("], \"dropped_instructions\": %llu, \"dropped_accesses\": %llu, "
           "\"dropped_transfers\": %llu}",
           (unsigned long long)r->dropped_insts, (unsigned long long)r->dropped_accesses,
           (unsigned long long)r->dropped_transfers);
}

static void emit(void) {
    uint32_t version_num = 0;
    const char *version = qbdi_getVersion(&version_num);
    int translated = -1;
    size_t len = sizeof(translated);
    if (sysctlbyname("sysctl.proc_translated", &translated, &len, NULL, 0) != 0) translated = -1;
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    printf("{\"qbdi_version\": \"%s\", \"qbdi_architecture\": \"%s\", \"qbdi_platform\": "
           "\"%s\", \"proc_translated\": %d, ",
           version, QBDI_ARCHITECTURE_STRING, QBDI_PLATFORM_STRING, translated);
    printf("\"symbols\": {\"tr0_chase\": \"0x%llx\", \"tr0_scale\": \"0x%llx\", "
           "\"scope_start\": \"0x%llx\", \"scope_end\": \"0x%llx\"}, ",
           (unsigned long long)(uintptr_t)tr0_chase, (unsigned long long)(uintptr_t)tr0_scale,
           (unsigned long long)(uintptr_t)&tr0_scope_start,
           (unsigned long long)(uintptr_t)&tr0_scope_end);
    printf("\"native\": {\"result\": %lld, \"out_value\": %lld, \"wall_ns\": %llu}, ",
           (long long)native_result, (long long)native_out, (unsigned long long)native_ns);
    printf("\"hook\": {\"installed_at_exit\": %s, \"removed\": %s}, ",
           hook_installed ? "true" : "false", hook_removed ? "true" : "false");
    printf("\"max_rss_bytes\": %ld, \"runs\": [", (long)usage.ru_maxrss);
    for (size_t i = 0; i < 2; i++) {
        if (i) printf(", ");
        emit_run(&runs[i]);
    }
    printf("]}\n");
    fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <head value> <tail value>\n", argv[0]);
        return 2;
    }
    head_value = strtoll(argv[1], NULL, 0);
    tail_value = strtoll(argv[2], NULL, 0);

    struct tr0_run native = {0};
    struct tr0_node *head;
    int64_t *out;
    build_inventory(&native, &head, &out);
    uint64_t t0 = now_ns();
    native_result = tr0_chase(head, out);
    native_ns = now_ns() - t0;
    native_out = *out;

    run_vm_call(&runs[0]);
    run_interception(&runs[1]);
    emit();
    return 0;
}
