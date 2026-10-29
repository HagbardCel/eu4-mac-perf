/*
 * Generic x86-64 invocation harness for self-contained synthetic subjects.
 *
 * One process performs one invocation. stdin declares the subject, the region
 * inventory (kind, size, original base, raw bytes or "-"), pointer edges,
 * register values and the exit registers to observe:
 *
 *   subject <name>
 *   region <heap|stack> <size> <original-base> <hex|->
 *   edge region <from> <offset> <to> <offset>
 *   edge register <name> <to> <offset>
 *   register <name> <value>
 *   observe <name>
 *   avoid
 *   end
 *
 * Every region is mapped at a fresh base; with "avoid" no mapping may overlap
 * any original range. Bytes are copied unchanged except at the declared edges,
 * which receive the new address of their target. RSP must be an edge into a
 * stack region; the call pushes the return address at that RSP. The subject is
 * entered through trace_harness_enter (entry.S), not a C function-pointer
 * call. stdout reports the bases, entry bytes and registers, the observed exit
 * registers and every region's final bytes.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MAX_REGIONS 16
#define MAX_EDGES 32
#define MAX_OBSERVE 16
#define MAX_SIZE 4096
#define MAX_REMAPS 16
#define RETURN_SLOT 16
#define RSP 7

typedef struct {
    uint64_t gpr[16];
} trace_harness_state;

trace_harness_state trace_harness_entry;
trace_harness_state trace_harness_exit;
void *trace_harness_target;

extern void trace_harness_enter(void);
extern void trace_subject_select(void);
extern void trace_subject_overlap(void);
extern void trace_subject_chase(void);
extern void trace_subject_alias(void);

static const struct {
    const char *name;
    void (*code)(void);
} subjects[] = {
    {"select", trace_subject_select},
    {"overlap", trace_subject_overlap},
    {"chase", trace_subject_chase},
    {"alias", trace_subject_alias},
};

static const char *const register_names[16] = {
    "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
    "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
};

typedef struct {
    int stack;
    size_t size;
    uint64_t original;
    int has_bytes;
    uint8_t bytes[MAX_SIZE];
    uint8_t *map;
    size_t map_size;
    uint8_t *base;
} region;

typedef struct {
    int from_register;
    unsigned from;
    size_t from_offset;
    unsigned to;
    size_t to_offset;
} edge;

typedef struct {
    void (*subject)(void);
    region regions[MAX_REGIONS];
    unsigned region_count;
    edge edges[MAX_EDGES];
    unsigned edge_count;
    unsigned observe[MAX_OBSERVE];
    unsigned observe_count;
    int avoid;
} request;

static request req;
static char word[2 * MAX_SIZE + 1];
static const char *parse_error;

static int fail(const char *message) {
    fprintf(stderr, "trace harness: %s\n", message);
    return 1;
}

static int next_word(void) {
    return scanf("%8192s", word) == 1;
}

static int read_number(uint64_t *value) {
    char *end = NULL;
    if (!next_word()) {
        return 0;
    }
    *value = strtoull(word, &end, 0);
    return end != word && *end == '\0';
}

static int read_register(unsigned *index) {
    if (!next_word()) {
        return 0;
    }
    for (unsigned i = 0; i < 16; i++) {
        if (strcmp(word, register_names[i]) == 0) {
            *index = i;
            return 1;
        }
    }
    return 0;
}

static int read_hex(region *g) {
    if (!next_word()) {
        return 0;
    }
    if (strcmp(word, "-") == 0) {
        g->has_bytes = 0;
        return 1;
    }
    if (strlen(word) != 2 * g->size) {
        return 0;
    }
    for (size_t i = 0; i < g->size; i++) {
        unsigned byte = 0;
        if (sscanf(word + 2 * i, "%2x", &byte) != 1) {
            return 0;
        }
        g->bytes[i] = (uint8_t)byte;
    }
    g->has_bytes = 1;
    return 1;
}

static int synthetic_subject_name(const char *name) {
    if (*name < 'a' || *name > 'z') {
        return 0;
    }
    for (const char *p = name; *p != '\0'; p++) {
        if ((*p < 'a' || *p > 'z') && (*p < '0' || *p > '9') && *p != '_') {
            return 0;
        }
    }
    return 1;
}

static void *linked_subject(const char *name) {
    char symbol[64];
    if (!synthetic_subject_name(name)) {
        return NULL;
    }
    if (snprintf(symbol, sizeof(symbol), "trace_subject_%s", name) >= (int)sizeof(symbol)) {
        return NULL;
    }
    return dlsym(RTLD_DEFAULT, symbol);
}

static int parse(void) {
    while (next_word()) {
        if (strcmp(word, "end") == 0) {
            return req.subject != NULL && req.region_count > 0;
        }
        if (strcmp(word, "subject") == 0) {
            if (!next_word()) {
                return 0;
            }
            for (size_t i = 0; i < sizeof(subjects) / sizeof(subjects[0]); i++) {
                if (strcmp(word, subjects[i].name) == 0) {
                    req.subject = subjects[i].code;
                }
            }
            if (req.subject == NULL) {
                req.subject = linked_subject(word);
                if (req.subject == NULL) {
                    parse_error = "synthetic subject symbol was not found";
                    return 0;
                }
            }
        } else if (strcmp(word, "region") == 0) {
            if (req.region_count == MAX_REGIONS || !next_word()) {
                return 0;
            }
            region *g = &req.regions[req.region_count];
            if (strcmp(word, "stack") == 0) {
                g->stack = 1;
            } else if (strcmp(word, "heap") != 0) {
                return 0;
            }
            uint64_t size = 0;
            if (!read_number(&size) || size == 0 || size > MAX_SIZE ||
                !read_number(&g->original)) {
                return 0;
            }
            g->size = (size_t)size;
            if (!read_hex(g)) {
                return 0;
            }
            req.region_count++;
        } else if (strcmp(word, "edge") == 0) {
            if (req.edge_count == MAX_EDGES || !next_word()) {
                return 0;
            }
            edge *e = &req.edges[req.edge_count];
            uint64_t from = 0, from_offset = 0, to = 0, to_offset = 0;
            if (strcmp(word, "register") == 0) {
                e->from_register = 1;
                if (!read_register(&e->from)) {
                    return 0;
                }
            } else if (strcmp(word, "region") == 0) {
                if (!read_number(&from) || !read_number(&from_offset)) {
                    return 0;
                }
                e->from = (unsigned)from;
                e->from_offset = (size_t)from_offset;
            } else {
                return 0;
            }
            if (!read_number(&to) || !read_number(&to_offset)) {
                return 0;
            }
            e->to = (unsigned)to;
            e->to_offset = (size_t)to_offset;
            req.edge_count++;
        } else if (strcmp(word, "register") == 0) {
            unsigned index = 0;
            if (!read_register(&index) ||
                !read_number(&trace_harness_entry.gpr[index])) {
                return 0;
            }
        } else if (strcmp(word, "observe") == 0) {
            if (req.observe_count == MAX_OBSERVE ||
                !read_register(&req.observe[req.observe_count])) {
                return 0;
            }
            req.observe_count++;
        } else if (strcmp(word, "avoid") == 0) {
            req.avoid = 1;
        } else {
            return 0;
        }
    }
    return 0;
}

static int check_edges(void) {
    int stack_edge = 0;
    for (unsigned i = 0; i < req.edge_count; i++) {
        const edge *e = &req.edges[i];
        if (e->to >= req.region_count ||
            e->to_offset > req.regions[e->to].size) {
            return 0;
        }
        if (e->from_register) {
            if (e->from == RSP) {
                if (!req.regions[e->to].stack) {
                    return 0;
                }
                stack_edge = 1;
            }
        } else if (e->from >= req.region_count ||
                   e->from_offset + 8 > req.regions[e->from].size) {
            return 0;
        }
    }
    return stack_edge;
}

static int overlaps_original(uint64_t low, uint64_t high) {
    if (!req.avoid) {
        return 0;
    }
    for (unsigned i = 0; i < req.region_count; i++) {
        const region *g = &req.regions[i];
        if (low < g->original + g->size && g->original < high) {
            return 1;
        }
    }
    return 0;
}

/* The stack region is placed so that RSP + 8 is 16-byte aligned at entry. */
static size_t stack_pad(unsigned index) {
    for (unsigned i = 0; i < req.edge_count; i++) {
        const edge *e = &req.edges[i];
        if (e->from_register && e->from == RSP && e->to == index) {
            return (24u - e->to_offset % 16u) % 16u;
        }
    }
    return 0;
}

static int map_regions(void) {
    long page_value = sysconf(_SC_PAGESIZE);
    if (page_value <= 0) {
        return fail("page size is unavailable");
    }
    size_t page = (size_t)page_value;
    for (unsigned i = 0; i < req.region_count; i++) {
        region *g = &req.regions[i];
        size_t pad = stack_pad(i);
        size_t want = pad + g->size + RETURN_SLOT;
        g->map_size = (want + page - 1) / page * page;
        void *rejected[MAX_REMAPS];
        unsigned rejected_count = 0;
        for (;;) {
            void *map = mmap(NULL, g->map_size, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANON, -1, 0);
            if (map == MAP_FAILED) {
                return fail("cannot map a region");
            }
            uint64_t low = (uint64_t)(uintptr_t)map;
            if (!overlaps_original(low, low + g->map_size)) {
                g->map = map;
                break;
            }
            if (rejected_count == MAX_REMAPS) {
                return fail("cannot map a region away from the original ranges");
            }
            rejected[rejected_count++] = map;
        }
        for (unsigned r = 0; r < rejected_count; r++) {
            (void)munmap(rejected[r], g->map_size);
        }
        g->base = g->map + pad;
        if (g->has_bytes) {
            memcpy(g->base, g->bytes, g->size);
        }
    }
    return 0;
}

static void apply_edges(void) {
    for (unsigned i = 0; i < req.edge_count; i++) {
        const edge *e = &req.edges[i];
        uint64_t value =
            (uint64_t)(uintptr_t)(req.regions[e->to].base + e->to_offset);
        if (e->from_register) {
            trace_harness_entry.gpr[e->from] = value;
        } else {
            memcpy(req.regions[e->from].base + e->from_offset, &value,
                   sizeof(value));
        }
    }
}

static void print_bytes(const char *label, unsigned index, const uint8_t *bytes,
                        size_t size) {
    printf("%s %u ", label, index);
    for (size_t i = 0; i < size; i++) {
        printf("%02x", bytes[i]);
    }
    printf("\n");
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "probe") == 0) {
        printf("ok\n");
        return 0;
    }
    if (argc != 1) {
        return fail("usage: harness [probe] < request");
    }
    if (!parse()) {
        return fail(parse_error != NULL ? parse_error : "malformed request");
    }
    if (!check_edges()) {
        return fail("edges are invalid or RSP has no edge into a stack region");
    }
    if (map_regions() != 0) {
        return 1;
    }
    apply_edges();

    for (unsigned i = 0; i < req.region_count; i++) {
        const region *g = &req.regions[i];
        printf("base %u 0x%llx\n", i, (unsigned long long)(uintptr_t)g->base);
        print_bytes("entry", i, g->base, g->size);
    }
    for (unsigned i = 0; i < 16; i++) {
        printf("entry_register %s 0x%llx\n", register_names[i],
               (unsigned long long)trace_harness_entry.gpr[i]);
    }

    trace_harness_target = (void *)(uintptr_t)req.subject;
    trace_harness_enter();

    if (trace_harness_exit.gpr[RSP] != trace_harness_entry.gpr[RSP] + 8) {
        return fail("the subject did not return through the declared boundary");
    }
    for (unsigned i = 0; i < req.observe_count; i++) {
        unsigned index = req.observe[i];
        printf("register %s 0x%llx\n", register_names[index],
               (unsigned long long)trace_harness_exit.gpr[index]);
    }
    for (unsigned i = 0; i < req.region_count; i++) {
        print_bytes("region", i, req.regions[i].base, req.regions[i].size);
    }
    printf("ok\n");
    return 0;
}
