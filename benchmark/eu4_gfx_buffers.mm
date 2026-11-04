#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <new>
#import <pthread.h>
#import <string.h>

/* Entries outlive the backend. The buffer is strong. The backend pointer is not. */
@interface EU4VertexBuffer : NSObject
@property (nonatomic) void *header;
@property (nonatomic, strong) id<MTLBuffer> buffer;
@property (nonatomic) size_t length;
@property (nonatomic) void *backend;
@end

@implementation EU4VertexBuffer
@end

/* Second registry. Same shape as EU4VertexBuffer, keyed by the 8-byte index header. */
@interface EU4IndexBuffer : NSObject
@property (nonatomic) void *header;
@property (nonatomic, strong) id<MTLBuffer> buffer;
@property (nonatomic) size_t length;
@property (nonatomic) int32_t count;
@property (nonatomic) void *backend;
@end

@implementation EU4IndexBuffer
@end

static NSMutableArray<EU4VertexBuffer *> *g_entries;
static NSMutableArray<EU4IndexBuffer *> *g_index_entries;
static pthread_t g_thread;
static int g_thread_set;
static int g_fail_metal;
static int g_fail_metal_throw;
static int g_fail_entry;
static int g_fail_insert;
static int g_fail_header;
static int g_create_recorded;
static int g_index_create_recorded;
static void *g_index_current_header;
static id<MTLBuffer> g_index_current_buffer;
static void *g_vertex_slots[EU4_GFX_VERTEX_SLOTS];
static int32_t g_disabled_attribs[32];
static int g_disabled_count;

/* The trusted events header declares this; the local copy predates it. */
extern "C" int eu4_metal_events_index_create(const void *caller, uintptr_t resource, uint64_t bytes,
                                             int32_t count);

static int on_gfx_thread(void) {
    return g_thread_set && pthread_equal(pthread_self(), g_thread);
}

void eu4_gfx_buffers_bind_thread(void) {
    g_thread = pthread_self();
    g_thread_set = 1;
}

int eu4_gfx_buffers_on_thread(void) {
    return on_gfx_thread();
}

static EU4VertexBuffer *find_entry(void *header) {
    for (EU4VertexBuffer *entry in g_entries) {
        if (entry.header == header) {
            return entry;
        }
    }
    return nil;
}

static EU4IndexBuffer *find_index_entry(void *header) {
    for (EU4IndexBuffer *entry in g_index_entries) {
        if (entry.header == header) {
            return entry;
        }
    }
    return nil;
}

static int logical_bytes(int32_t count, int32_t stride, size_t *out) {
    if (count < 0 || stride < 0) {
        return -1;
    }
    int64_t product = (int64_t)count * (int64_t)stride;
    if (product > 0x7fffffff) {
        return -1;
    }
    *out = (size_t)product;
    return 0;
}

/* shortcut: zero-length logical buffers use a 1-byte backing store, until a caller requires a true empty allocation. */
static id<MTLBuffer> make_buffer(id<MTLDevice> device, const void *data, size_t logical) {
    if (g_fail_metal_throw) {
        @throw [NSException exceptionWithName:NSMallocException reason:@"metal" userInfo:nil];
    }
    if (g_fail_metal) {
        return nil;
    }
    NSUInteger physical = logical == 0 ? 1 : logical;
    MTLResourceOptions options = MTLResourceStorageModeShared;
    if (data != NULL && logical > 0) {
        return [device newBufferWithBytes:data length:physical options:options];
    }
    return [device newBufferWithLength:physical options:options];
}

static void *new_header(const void *caller) {
    if (g_fail_header) {
        eu4_gfx_stop("_GfxCreateVertexBuffer", "header", caller);
    }
    try {
        return ::operator new(12);
    } catch (const std::bad_alloc &) {
        eu4_gfx_stop("_GfxCreateVertexBuffer", "header", caller);
    }
}

__attribute__((noreturn)) static void abandon_create(EU4VertexBuffer *entry, id<MTLBuffer> buffer,
                                                       void *header, const char *reason,
                                                       const void *caller) {
    if (entry != nil && g_entries != nil) {
        NSUInteger index = [g_entries indexOfObjectIdenticalTo:entry];
        if (index != NSNotFound) {
            [g_entries removeObjectAtIndex:index];
        }
    }
    if (entry != nil) {
        entry.header = NULL;
        entry.buffer = nil;
    }
    buffer = nil;
    (void)buffer;
    if (header != NULL) {
        ::operator delete(header);
    }
    eu4_gfx_stop("_GfxCreateVertexBuffer", reason, caller);
}

static void clear_vertex_slot(void *header) {
    if (header == NULL) {
        return;
    }
    for (int slot = 0; slot < EU4_GFX_VERTEX_SLOTS; slot++) {
        if (g_vertex_slots[slot] == header) {
            g_vertex_slots[slot] = NULL;
        }
    }
}

static void require_thread(const char *symbol, const void *caller) {
    if (!on_gfx_thread()) {
        eu4_gfx_stop(symbol, "thread", caller);
    }
}

void eu4_gfx_require_thread(const char *symbol, const void *caller) {
    require_thread(symbol, caller);
}

void eu4_gfx_release_vertex_buffers(void *backend, const void *caller) {
    if (!g_thread_set || backend == NULL) {
        return;
    }
    if (!on_gfx_thread()) {
        eu4_gfx_stop("_GfxShutdown", "thread", caller);
    }
    for (EU4VertexBuffer *entry in g_entries) {
        if (entry.backend == backend) {
            entry.buffer = nil;
        }
    }
    memset(g_vertex_slots, 0, sizeof g_vertex_slots);
    g_disabled_count = 0;
    for (EU4IndexBuffer *entry in g_index_entries) {
        if (entry.backend == backend) {
            entry.buffer = nil;
            if (g_index_current_header == entry.header) {
                g_index_current_header = NULL;
                g_index_current_buffer = nil;
            }
        }
    }
}

void *eu4_gfx_create_vertex_buffer(void *ignored, const void *data, int32_t count, int32_t stride,
                                   int32_t usage) {
    const void *caller = __builtin_return_address(0);
    (void)ignored;
    (void)usage;
    if (g_thread_set && !on_gfx_thread()) {
        eu4_gfx_stop("_GfxCreateVertexBuffer", "thread", caller);
    }
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    void *backend = eu4_gfx_backend_token();
    if (device == nil || backend == NULL || eu4_gfx_image() == NULL) {
        eu4_gfx_stop("_GfxCreateVertexBuffer", "no_backend", caller);
    }
    size_t logical = 0;
    if (logical_bytes(count, stride, &logical) != 0) {
        eu4_gfx_stop("_GfxCreateVertexBuffer", "size", caller);
    }
    void *header = new_header(caller);
    int32_t count_bits = count;
    int32_t stride_bits = stride;
    uint32_t name = 0;
    memcpy(header, &stride_bits, 4);
    memcpy((uint8_t *)header + 4, &count_bits, 4);
    memcpy((uint8_t *)header + 8, &name, 4);
    id<MTLBuffer> buffer = nil;
    EU4VertexBuffer *entry = nil;
    @try {
        buffer = make_buffer(device, data, logical);
        if (buffer == nil) {
            ::operator delete(header);
            header = NULL;
            eu4_gfx_stop("_GfxCreateVertexBuffer", "metal", caller);
        }
        if (g_fail_entry) {
            @throw [NSException exceptionWithName:NSMallocException reason:@"entry" userInfo:nil];
        }
        entry = [EU4VertexBuffer new];
        entry.header = header;
        entry.buffer = buffer;
        entry.length = logical;
        entry.backend = backend;
        if (g_fail_insert) {
            @throw [NSException exceptionWithName:NSMallocException reason:@"insert" userInfo:nil];
        }
        if (g_entries == nil) {
            g_entries = [NSMutableArray array];
        }
        [g_entries addObject:entry];
    } @catch (NSException *exception) {
        const char *reason = "alloc";
        if ([exception.reason isEqualToString:@"metal"]) {
            reason = "metal";
        } else if ([exception.reason isEqualToString:@"entry"]) {
            reason = "entry";
        } else if ([exception.reason isEqualToString:@"insert"]) {
            reason = "insert";
        }
        abandon_create(entry, buffer, header, reason, caller);
    }
    int32_t *counter = (int32_t *)(eu4_gfx_image() + EU4_GFX_VB_COUNT_RVA);
    *counter += 1;
    if (!g_create_recorded && eu4_metal_events_create(caller) == 0) {
        g_create_recorded = 1;
    }
    uint32_t token = 1;
    memcpy((uint8_t *)header + 8, &token, 4);
    return header;
}

/* Offset is r8d. The sealed body copies r8d, then zero-extends it as the subdata offset. */
void eu4_gfx_update_vertex_buffer(void *handle, void *header, const void *data, int32_t count,
                                  uint32_t offset, int32_t partial) {
    const void *caller = __builtin_return_address(0);
    require_thread("_GfxUpdateVertexBuffer", caller);
    EU4VertexBuffer *entry = find_entry(header);
    if (entry == nil) {
        eu4_gfx_stop("_GfxUpdateVertexBuffer", "missing", caller);
    }
    if (entry.buffer == nil) {
        eu4_gfx_stop("_GfxUpdateVertexBuffer", "released", caller);
    }
    if (count <= 0) {
        memcpy(&count, (uint8_t *)header + 4, 4);
    }
    int32_t stride = 0;
    memcpy(&stride, header, 4);
    size_t logical = 0;
    if (logical_bytes(count, stride, &logical) != 0) {
        eu4_gfx_stop("_GfxUpdateVertexBuffer", "size", caller);
    }
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    if (master == NULL) {
        eu4_gfx_stop("_GfxUpdateVertexBuffer", "master", caller);
    }
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (partial == 0) {
        if (data == NULL || logical != entry.length) {
            if (device == nil) {
                eu4_gfx_stop("_GfxUpdateVertexBuffer", "no_backend", caller);
            }
            id<MTLBuffer> fresh = nil;
            @try {
                fresh = make_buffer(device, data, logical);
            } @catch (NSException *exception) {
                (void)exception;
                eu4_gfx_stop("_GfxUpdateVertexBuffer", "metal", caller);
            }
            if (fresh == nil) {
                eu4_gfx_stop("_GfxUpdateVertexBuffer", "metal", caller);
            }
            entry.buffer = fresh;
            entry.length = logical;
        } else {
            memcpy(entry.buffer.contents, data, logical);
        }
    } else {
        if (data == NULL && logical > 0) {
            eu4_gfx_stop("_GfxUpdateVertexBuffer", "null_data", caller);
        }
        if ((size_t)offset > entry.length || logical > entry.length - (size_t)offset) {
            eu4_gfx_stop("_GfxUpdateVertexBuffer", "bounds", caller);
        }
        if (logical > 0) {
            memcpy((uint8_t *)entry.buffer.contents + offset, data, logical);
        }
    }
    ((uint8_t *)master)[0x170] = 1;
}

void eu4_gfx_destroy_vertex_buffer(void *header) {
    const void *caller = __builtin_return_address(0);
    if (header == NULL) {
        return;
    }
    require_thread("_GfxDestroyVertexBuffer", caller);
    NSUInteger index = NSNotFound;
    EU4VertexBuffer *entry = nil;
    NSUInteger count = g_entries.count;
    for (NSUInteger i = 0; i < count; i++) {
        EU4VertexBuffer *candidate = g_entries[i];
        if (candidate.header == header) {
            entry = candidate;
            index = i;
            break;
        }
    }
    if (entry == nil) {
        eu4_gfx_stop("_GfxDestroyVertexBuffer", "missing", caller);
    }
    uint8_t *image = eu4_gfx_image();
    if (image == NULL) {
        eu4_gfx_stop("_GfxDestroyVertexBuffer", "counter", caller);
    }
    entry.buffer = nil;
    entry.header = NULL;
    [g_entries removeObjectAtIndex:index];
    clear_vertex_slot(header);
    ::operator delete(header);
    int32_t *counter = (int32_t *)(image + EU4_GFX_VB_COUNT_RVA);
    *counter -= 1;
}

int eu4_gfx_vertex_records(void) { return g_entries == nil ? 0 : (int)g_entries.count; }

int eu4_gfx_vertex_gpus(void) {
    int n = 0;
    for (EU4VertexBuffer *entry in g_entries) {
        if (entry.buffer != nil) {
            n += 1;
        }
    }
    return n;
}

size_t eu4_gfx_vertex_buffer_length(void *header) {
    EU4VertexBuffer *entry = find_entry(header);
    return entry == nil ? (size_t)-1 : entry.length;
}

int eu4_gfx_vertex_buffer_read(void *header, void *dest, size_t n) {
    EU4VertexBuffer *entry = find_entry(header);
    if (entry == nil || entry.buffer == nil || entry.length != n) {
        return -1;
    }
    if (n > 0) {
        memcpy(dest, entry.buffer.contents, n);
    }
    return 0;
}

int eu4_gfx_vertex_buffer_shared(void *header) {
    EU4VertexBuffer *entry = find_entry(header);
    return entry != nil && entry.buffer != nil && entry.buffer.storageMode == MTLStorageModeShared;
}

static void *new_index_header(const void *caller, int *recycled) {
    if (g_fail_header) {
        eu4_gfx_stop("_GfxCreateIndexBuffer", "header", caller);
    }
    uint8_t *image = eu4_gfx_image();
    int32_t top = 0;
    memcpy(&top, image + EU4_GFX_IB_CACHE_TOP_RVA, 4);
    if (top > 0 && top <= EU4_GFX_IB_CACHE_SLOTS) {
        top -= 1;
        memcpy(image + EU4_GFX_IB_CACHE_TOP_RVA, &top, 4);
        void *header = NULL;
        memcpy(&header, image + EU4_GFX_IB_CACHE_RVA + (size_t)top * sizeof header, sizeof header);
        *recycled = 1;
        return header;
    }
    try {
        return ::operator new(8);
    } catch (const std::bad_alloc &) {
        eu4_gfx_stop("_GfxCreateIndexBuffer", "header", caller);
    }
    return NULL;
}

/* Destroy pushes while the top is <= 0x7f, otherwise the header is freed. */
static void recycle_index_header(void *header) {
    uint8_t *image = eu4_gfx_image();
    int32_t top = 0;
    memcpy(&top, image + EU4_GFX_IB_CACHE_TOP_RVA, 4);
    if (top >= 0 && top <= 0x7f) {
        memcpy(image + EU4_GFX_IB_CACHE_RVA + (size_t)top * sizeof header, &header, sizeof header);
        top += 1;
        memcpy(image + EU4_GFX_IB_CACHE_TOP_RVA, &top, 4);
    } else {
        ::operator delete(header);
    }
}

__attribute__((noreturn)) static void abandon_index_create(EU4IndexBuffer *entry,
                                                           id<MTLBuffer> buffer, void *header,
                                                           int recycled, const char *reason,
                                                           const void *caller) {
    if (entry != nil && g_index_entries != nil) {
        NSUInteger index = [g_index_entries indexOfObjectIdenticalTo:entry];
        if (index != NSNotFound) {
            [g_index_entries removeObjectAtIndex:index];
        }
    }
    if (entry != nil) {
        entry.header = NULL;
        entry.buffer = nil;
    }
    buffer = nil;
    (void)buffer;
    if (header != NULL) {
        if (recycled) {
            recycle_index_header(header);
        } else {
            ::operator delete(header);
        }
    }
    eu4_gfx_stop("_GfxCreateIndexBuffer", reason, caller);
}

void *eu4_gfx_create_index_buffer(void *ignored, const void *data, int32_t count) {
    const void *caller = __builtin_return_address(0);
    (void)ignored;
    if (g_thread_set && !on_gfx_thread()) {
        eu4_gfx_stop("_GfxCreateIndexBuffer", "thread", caller);
    }
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    void *backend = eu4_gfx_backend_token();
    if (device == nil || backend == NULL || eu4_gfx_image() == NULL) {
        eu4_gfx_stop("_GfxCreateIndexBuffer", "no_backend", caller);
    }
    size_t length = 0;
    if (logical_bytes(count, 2, &length) != 0) {
        eu4_gfx_stop("_GfxCreateIndexBuffer", "size", caller);
    }
    int recycled = 0;
    void *header = new_index_header(caller, &recycled);
    int32_t count_bits = count;
    uint32_t name = 1;
    memcpy(header, &count_bits, 4);
    memcpy((uint8_t *)header + 4, &name, 4);
    id<MTLBuffer> buffer = nil;
    EU4IndexBuffer *entry = nil;
    @try {
        buffer = make_buffer(device, data, length);
        if (buffer == nil) {
            abandon_index_create(nil, nil, header, recycled, "metal", caller);
        }
        if (g_fail_entry) {
            @throw [NSException exceptionWithName:NSMallocException reason:@"entry" userInfo:nil];
        }
        entry = [EU4IndexBuffer new];
        entry.header = header;
        entry.buffer = buffer;
        entry.length = length;
        entry.count = count_bits;
        entry.backend = backend;
        if (g_fail_insert) {
            @throw [NSException exceptionWithName:NSMallocException reason:@"insert" userInfo:nil];
        }
        if (g_index_entries == nil) {
            g_index_entries = [NSMutableArray array];
        }
        [g_index_entries addObject:entry];
    } @catch (NSException *exception) {
        const char *reason = "alloc";
        if ([exception.reason isEqualToString:@"metal"]) {
            reason = "metal";
        } else if ([exception.reason isEqualToString:@"entry"]) {
            reason = "entry";
        } else if ([exception.reason isEqualToString:@"insert"]) {
            reason = "insert";
        }
        abandon_index_create(entry, buffer, header, recycled, reason, caller);
    }
    int32_t *counter = (int32_t *)(eu4_gfx_image() + EU4_GFX_IB_COUNT_RVA);
    *counter += 1;
    if (!g_index_create_recorded &&
        eu4_metal_events_index_create(caller, (uintptr_t)(__bridge void *)buffer, length, count) == 0) {
        g_index_create_recorded = 1;
    }
    return header;
}

void eu4_gfx_update_index_buffer(void *ignored, void *header, const void *data, int32_t count) {
    const void *caller = __builtin_return_address(0);
    (void)ignored;
    require_thread("_GfxUpdateIndexBuffer", caller);
    EU4IndexBuffer *entry = find_index_entry(header);
    if (entry == nil) {
        eu4_gfx_stop("_GfxUpdateIndexBuffer", "missing", caller);
    }
    uint32_t name = 0;
    memcpy(&name, (uint8_t *)header + 4, 4);
    if (name == 0) {
        return;
    }
    if (entry.buffer == nil) {
        eu4_gfx_stop("_GfxUpdateIndexBuffer", "released", caller);
    }
    int32_t effective = count < 1 ? entry.count : count;
    size_t length = 0;
    if (logical_bytes(effective, 2, &length) != 0) {
        eu4_gfx_stop("_GfxUpdateIndexBuffer", "size", caller);
    }
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    id<MTLBuffer> fresh = nil;
    @try {
        fresh = make_buffer(device, data, length);
    } @catch (NSException *exception) {
        (void)exception;
        eu4_gfx_stop("_GfxUpdateIndexBuffer", "metal", caller);
    }
    if (fresh == nil) {
        eu4_gfx_stop("_GfxUpdateIndexBuffer", "metal", caller);
    }
    entry.buffer = fresh;
    entry.length = length;
    if (g_index_current_header == header) {
        g_index_current_buffer = fresh;
    }
}

void eu4_gfx_destroy_index_buffer(void *header) {
    const void *caller = __builtin_return_address(0);
    if (header == NULL) {
        return;
    }
    require_thread("_GfxDestroyIndexBuffer", caller);
    EU4IndexBuffer *entry = find_index_entry(header);
    if (entry == nil) {
        eu4_gfx_stop("_GfxDestroyIndexBuffer", "missing", caller);
    }
    uint8_t *image = eu4_gfx_image();
    if (image == NULL) {
        eu4_gfx_stop("_GfxDestroyIndexBuffer", "counter", caller);
    }
    entry.buffer = nil;
    entry.header = NULL;
    NSUInteger index = [g_index_entries indexOfObjectIdenticalTo:entry];
    if (index != NSNotFound) {
        [g_index_entries removeObjectAtIndex:index];
    }
    if (g_index_current_header == header) {
        g_index_current_header = NULL;
        g_index_current_buffer = nil;
    }
    recycle_index_header(header);
    int32_t *counter = (int32_t *)(image + EU4_GFX_IB_COUNT_RVA);
    *counter -= 1;
}

void eu4_gfx_set_index_buffer(void *handle, void *header) {
    const void *caller = __builtin_return_address(0);
    if (header == NULL) {
        return;
    }
    require_thread("_GfxSetIndexBuffer", caller);
    EU4IndexBuffer *entry = find_index_entry(header);
    if (entry == nil) {
        eu4_gfx_stop("_GfxSetIndexBuffer", "missing", caller);
    }
    if (entry.buffer == nil) {
        eu4_gfx_stop("_GfxSetIndexBuffer", "released", caller);
    }
    g_index_current_header = header;
    g_index_current_buffer = entry.buffer;
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    if (master != NULL) {
        int32_t stored = entry.count;
        memcpy((uint8_t *)master + 0x68, &stored, 4);
    }
}

int eu4_gfx_index_records(void) { return g_index_entries == nil ? 0 : (int)g_index_entries.count; }

int eu4_gfx_index_gpus(void) {
    int n = 0;
    for (EU4IndexBuffer *entry in g_index_entries) {
        if (entry.buffer != nil) {
            n += 1;
        }
    }
    return n;
}

size_t eu4_gfx_index_buffer_length(void *header) {
    EU4IndexBuffer *entry = find_index_entry(header);
    return entry == nil ? (size_t)-1 : entry.length;
}

int eu4_gfx_index_buffer_read(void *header, void *dest, size_t n) {
    EU4IndexBuffer *entry = find_index_entry(header);
    if (entry == nil || entry.buffer == nil || entry.length != n) {
        return -1;
    }
    if (n > 0) {
        memcpy(dest, entry.buffer.contents, n);
    }
    return 0;
}

int eu4_gfx_index_buffer_shared(void *header) {
    EU4IndexBuffer *entry = find_index_entry(header);
    return entry != nil && entry.buffer != nil && entry.buffer.storageMode == MTLStorageModeShared;
}

void *eu4_gfx_current_index_header(void) { return g_index_current_header; }

static void record_disabled_attribs(void *master, const void *caller) {
    g_disabled_count = 0;
    void *shader = NULL;
    memcpy(&shader, (uint8_t *)master + 0x70, sizeof shader);
    if (shader == NULL) {
        return;
    }
    int32_t count = 0;
    memcpy(&count, (uint8_t *)shader + 0x18, 4);
    if (count <= 0) {
        return;
    }
    if (count > 32) {
        eu4_gfx_stop("_GfxSetVertexBuffers", "attrib", caller);
    }
    void *base = NULL;
    memcpy(&base, (uint8_t *)shader + 0x10, sizeof base);
    if (base == NULL) {
        eu4_gfx_stop("_GfxSetVertexBuffers", "attrib", caller);
    }
    int32_t offset = count * 0x28 - 0x1c;
    for (int left = count + 1; left > 1; left--) {
        int32_t index = 0;
        memcpy(&index, (uint8_t *)base + offset, 4);
        g_disabled_attribs[g_disabled_count++] = index;
        offset -= 0x28;
    }
}

static void note_null_header(const void *caller) {
    uint8_t *image = eu4_gfx_image();
    if (image == NULL) {
        eu4_gfx_stop("_GfxSetVertexBuffers", "image", caller);
    }
    uint8_t *flag = image + EU4_GFX_VB_ERROR_RVA;
    if (*flag == 0) {
        *flag = 1;
    }
}

void eu4_gfx_set_vertex_buffers(void *handle, int32_t count, void **headers, int32_t start,
                                const int32_t *offsets, const int32_t *words) {
    const void *caller = __builtin_return_address(0);
    require_thread("_GfxSetVertexBuffers", caller);
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    if (master == NULL) {
        eu4_gfx_stop("_GfxSetVertexBuffers", "master", caller);
    }
    if (count < 0 || (count > 0 && headers == NULL)) {
        eu4_gfx_stop("_GfxSetVertexBuffers", "range", caller);
    }
    if (count > 0) {
        int64_t last = (int64_t)start + (int64_t)count - 1;
        if (start < 0 || last >= EU4_GFX_VERTEX_SLOTS) {
            eu4_gfx_stop("_GfxSetVertexBuffers", "range", caller);
        }
    }
    record_disabled_attribs(master, caller);
    for (int32_t i = 0; i < count; i++) {
        void *header = headers[i];
        int32_t slot = start + i;
        if (header == NULL) {
            note_null_header(caller);
            continue;
        }
        int32_t name = 0;
        memcpy(&name, (uint8_t *)header + 8, 4);
        if (name == 0) {
            continue;
        }
        int32_t offset = 0;
        if (offsets != NULL) {
            memcpy(&offset, offsets + i, 4);
        }
        int32_t word = 0;
        if (words != NULL) {
            memcpy(&word, words + i, 4);
        } else {
            memcpy(&word, header, 4);
        }
        memcpy((uint8_t *)master + 0x130 + (size_t)slot * 8, &header, sizeof header);
        memcpy((uint8_t *)master + 0x150 + (size_t)slot * 4, &offset, 4);
        memcpy((uint8_t *)master + 0x160 + (size_t)slot * 4, &word, 4);
        if (slot == 0) {
            int32_t at_4 = 0;
            memcpy(&at_4, (uint8_t *)header + 4, 4);
            memcpy((uint8_t *)master + 0x64, &at_4, 4);
            memcpy((uint8_t *)master + 0x6c, &word, 4);
        }
        g_vertex_slots[slot] = header;
    }
    ((uint8_t *)master)[0x170] = 1;
}

void *eu4_gfx_vertex_slot_header(int slot) {
    if (slot < 0 || slot >= EU4_GFX_VERTEX_SLOTS) {
        return NULL;
    }
    return g_vertex_slots[slot];
}

void *eu4_gfx_vertex_slot_buffer(int slot) {
    EU4VertexBuffer *entry = find_entry(eu4_gfx_vertex_slot_header(slot));
    if (entry == nil || entry.buffer == nil) {
        return NULL;
    }
    return (__bridge void *)entry.buffer;
}

int eu4_gfx_vertex_slot_read(int slot, void *dest, size_t n) {
    EU4VertexBuffer *entry = find_entry(eu4_gfx_vertex_slot_header(slot));
    if (entry == nil || entry.buffer == nil || entry.length != n) {
        return -1;
    }
    if (n > 0 && dest == NULL) {
        return -1;
    }
    if (n > 0) {
        memcpy(dest, entry.buffer.contents, n);
    }
    return 0;
}

int eu4_gfx_disabled_vertex_attrib_count(void) { return g_disabled_count; }

int eu4_gfx_disabled_vertex_attrib(int index, int32_t *out) {
    if (index < 0 || index >= g_disabled_count || out == NULL) {
        return -1;
    }
    *out = g_disabled_attribs[index];
    return 0;
}

int eu4_gfx_current_index_read(void *dest, size_t n) {
    EU4IndexBuffer *entry = find_index_entry(g_index_current_header);
    if (entry == nil || entry.buffer == nil || g_index_current_buffer != entry.buffer || entry.length != n) {
        return -1;
    }
    if (n > 0 && dest == NULL) {
        return -1;
    }
    if (n > 0) {
        memcpy(dest, g_index_current_buffer.contents, n);
    }
    return 0;
}

void eu4_gfx_buffer_test_fail_metal(int on) { g_fail_metal = on; }
void eu4_gfx_buffer_test_fail_metal_throw(int on) { g_fail_metal_throw = on; }
void eu4_gfx_buffer_test_fail_entry(int on) { g_fail_entry = on; }
void eu4_gfx_buffer_test_fail_insert(int on) { g_fail_insert = on; }
void eu4_gfx_buffer_test_fail_header(int on) { g_fail_header = on; }
