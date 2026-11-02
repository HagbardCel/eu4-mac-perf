#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"

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

static NSMutableArray<EU4VertexBuffer *> *g_entries;
static pthread_t g_thread;
static int g_thread_set;
static int g_fail_metal;
static int g_fail_insert;
static int g_fail_header;

static int on_gfx_thread(void) {
    return g_thread_set && pthread_equal(pthread_self(), g_thread);
}

void eu4_gfx_buffers_bind_thread(void) {
    g_thread = pthread_self();
    g_thread_set = 1;
}

static EU4VertexBuffer *find_entry(void *header) {
    for (EU4VertexBuffer *entry in g_entries) {
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

__attribute__((noreturn)) static void drop_new(EU4VertexBuffer *entry, void *header, const void *caller) {
    if (g_entries != nil) {
        NSUInteger index = [g_entries indexOfObjectIdenticalTo:entry];
        if (index != NSNotFound) {
            [g_entries removeObjectAtIndex:index];
        }
    }
    entry.buffer = nil;
    entry.header = NULL;
    ::operator delete(header);
    eu4_gfx_stop("_GfxCreateVertexBuffer", "insert", caller);
}

static void require_thread(const char *symbol, const void *caller) {
    if (!on_gfx_thread()) {
        eu4_gfx_stop(symbol, "thread", caller);
    }
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
    id<MTLBuffer> buffer = make_buffer(device, data, logical);
    if (buffer == nil) {
        ::operator delete(header);
        eu4_gfx_stop("_GfxCreateVertexBuffer", "metal", caller);
    }
    EU4VertexBuffer *entry = [EU4VertexBuffer new];
    entry.header = header;
    entry.buffer = buffer;
    entry.length = logical;
    entry.backend = backend;
    @try {
        if (g_fail_insert) {
            @throw [NSException exceptionWithName:NSMallocException reason:@"insert" userInfo:nil];
        }
        if (g_entries == nil) {
            g_entries = [NSMutableArray array];
        }
        [g_entries addObject:entry];
    } @catch (NSException *exception) {
        (void)exception;
        drop_new(entry, header, caller);
    }
    int32_t *counter = (int32_t *)(eu4_gfx_image() + EU4_GFX_VB_COUNT_RVA);
    *counter += 1;
    return header;
}

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
            id<MTLBuffer> fresh = make_buffer(device, data, logical);
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

void eu4_gfx_buffer_test_fail_metal(int on) { g_fail_metal = on; }
void eu4_gfx_buffer_test_fail_insert(int on) { g_fail_insert = on; }
void eu4_gfx_buffer_test_fail_header(int on) { g_fail_header = on; }
