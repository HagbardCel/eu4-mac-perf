#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <CoreFoundation/CoreFoundation.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* shortcut: 64KiB per buffer allocation, raise if a later capture needs more. */
static const size_t kMaxBytes = 64 * 1024;
static const size_t kMaxLine = 256 * 1024;

@interface ShadowBuf : NSObject
@property (nonatomic) void *header;
@property (nonatomic) BOOL known;
@end

@implementation ShadowBuf
@end

static uint8_t *g_image;
static void *g_slot;
static void *g_master;
static CAMetalLayer *g_layer;
static uint32_t g_window_id = 7;
static uint32_t g_source = 9;
static int g_width = 800;
static int g_height = 600;
static int g_up;
static int g_did_init;
static int g_down;
static long long g_expect = 1;
static NSMutableDictionary<NSString *, ShadowBuf *> *g_live;
static NSMutableSet<NSString *> *g_ever;
static int g_fail_read;
static int g_fail_state;

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void *fake_window(uint32_t window_id) {
    if (window_id == 0) {
        return NULL;
    }
    return (void *)0x11;
}

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = g_width;
    *height = g_height;
}

static void reply(NSDictionary *body) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:body options:NSJSONWritingSortedKeys error:&error];
    if (data == nil) {
        fputs("{\"schema\":\"metal-shadow/v0\",\"ok\":false,\"error\":\"malformed\"}\n", stdout);
    } else {
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
    }
    fflush(stdout);
}

static void fail_env(NSString *error, id seq) {
    NSMutableDictionary *body = [@{
        @"schema": @"metal-shadow/v0",
        @"ok": @NO,
        @"error": error,
        @"next": @(g_expect),
    } mutableCopy];
    if (seq != nil) {
        body[@"seq"] = seq;
    }
    reply(body);
}

static void fail_sem(long long seq, NSString *op, NSString *error) {
    g_expect = seq + 1;
    NSMutableDictionary *body = [@{
        @"schema": @"metal-shadow/v0",
        @"ok": @NO,
        @"seq": @(seq),
        @"next": @(g_expect),
        @"error": error,
    } mutableCopy];
    if (op != nil) {
        body[@"op"] = op;
    }
    reply(body);
}

static BOOL json_i64(id value, long long *out) {
    if (![value isKindOfClass:[NSNumber class]]) {
        return NO;
    }
    if (CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) {
        return NO;
    }
    CFNumberRef number = (__bridge CFNumberRef)value;
    if (CFNumberIsFloatType(number)) {
        return NO;
    }
    long long got = 0;
    if (!CFNumberGetValue(number, kCFNumberSInt64Type, &got)) {
        return NO;
    }
    *out = got;
    return YES;
}

static BOOL json_i32(id value, int32_t *out) {
    long long got = 0;
    if (!json_i64(value, &got) || got < INT32_MIN || got > INT32_MAX) {
        return NO;
    }
    *out = (int32_t)got;
    return YES;
}

static BOOL json_u32(id value, uint32_t *out) {
    long long got = 0;
    if (!json_i64(value, &got) || got < 0 || got > (long long)UINT32_MAX) {
        return NO;
    }
    *out = (uint32_t)got;
    return YES;
}

static BOOL valid_id(NSString *ident) {
    if (![ident isKindOfClass:[NSString class]] || ident.length < 1 || ident.length > 64) {
        return NO;
    }
    for (NSUInteger i = 0; i < ident.length; i++) {
        unichar c = [ident characterAtIndex:i];
        BOOL ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (!ok) {
            return NO;
        }
    }
    return YES;
}

static int logical_size(int32_t count, int32_t stride, size_t *out) {
    if (count < 0 || stride < 0) {
        return -1;
    }
    int64_t product = 0;
    if (__builtin_mul_overflow((int64_t)count, (int64_t)stride, &product) || product > 0x7fffffff ||
        (uint64_t)product > kMaxBytes) {
        return -1;
    }
    *out = (size_t)product;
    return 0;
}

static NSString *id_for_header(void *header) {
    if (g_fail_state == 2) {
        return nil;
    }
    for (NSString *ident in g_live) {
        if (g_live[ident].header == header) {
            return ident;
        }
    }
    return nil;
}

static size_t observed_length(void *header) {
    if (g_fail_state == 1) {
        return (size_t)-1;
    }
    return eu4_gfx_vertex_buffer_length(header);
}

static NSString *hex_bytes(const uint8_t *bytes, size_t n) {
    static const char digits[] = "0123456789abcdef";
    if (n > 36) {
        return nil;
    }
    char text[36 * 2 + 1];
    for (size_t i = 0; i < n; i++) {
        text[i * 2] = digits[bytes[i] >> 4];
        text[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    text[n * 2] = 0;
    return [NSString stringWithUTF8String:text];
}

static int32_t load_i32(const void *base, int offset) {
    int32_t value = 0;
    memcpy(&value, (const uint8_t *)base + offset, 4);
    return value;
}

static NSDictionary *state_now(void) {
    NSMutableDictionary *state = [@{
        @"initialized": @(g_up ? YES : NO),
        @"phase": @(eu4_gfx_phase()),
        @"backend_releases": @(eu4_gfx_backend_releases()),
        @"backend_present": @(eu4_gfx_backend_token() != NULL ? YES : NO),
        @"live_count": @(g_live.count),
        @"vertex_records": @(eu4_gfx_vertex_records()),
        @"vertex_gpus": @(eu4_gfx_vertex_gpus()),
    } mutableCopy];
    if (!g_up || g_master == NULL) {
        return state;
    }
    int32_t view[6];
    memcpy(view, (uint8_t *)g_master + 0xac, sizeof view);
    NSMutableArray *viewport = [NSMutableArray arrayWithCapacity:6];
    for (int i = 0; i < 6; i++) {
        [viewport addObject:@(view[i])];
    }
    uint8_t blend[0x24], depth[0x24], raster[0x10];
    memcpy(blend, (uint8_t *)g_master + 0xc, sizeof blend);
    memcpy(depth, (uint8_t *)g_master + 0x30, sizeof depth);
    memcpy(raster, (uint8_t *)g_master + 0x54, sizeof raster);
    NSString *blend_snap = hex_bytes(blend, sizeof blend);
    NSString *depth_snap = hex_bytes(depth, sizeof depth);
    NSString *raster_snap = hex_bytes(raster, sizeof raster);
    if (blend_snap == nil || depth_snap == nil || raster_snap == nil) {
        return nil;
    }
    NSMutableArray *slots = [NSMutableArray arrayWithCapacity:4];
    for (int slot = 0; slot < EU4_GFX_VERTEX_SLOTS; slot++) {
        void *header = eu4_gfx_vertex_slot_header(slot);
        if (header == NULL) {
            [slots addObject:@{
                @"slot": @(slot),
                @"resource_id": [NSNull null],
                @"length": @0,
            }];
            continue;
        }
        size_t length = observed_length(header);
        NSString *ident = id_for_header(header);
        if (length == (size_t)-1 || ident == nil) {
            return nil;
        }
        [slots addObject:@{
            @"slot": @(slot),
            @"resource_id": ident,
            @"length": @(length),
        }];
    }
    state[@"width"] = @(load_i32(g_master, 0xa0));
    state[@"height"] = @(load_i32(g_master, 0xa4));
    state[@"source"] = @(load_i32(g_master, 0xa8));
    state[@"primitive"] = @(load_i32(g_master, 8));
    state[@"max_texture"] = @(load_i32(g_master, 0x188));
    state[@"texture_lod"] = @(((uint8_t *)g_master)[0x18e]);
    state[@"dirty"] = @(((uint8_t *)g_master)[0x170]);
    state[@"viewport"] = viewport;
    state[@"blend_snap"] = blend_snap;
    state[@"depth_snap"] = depth_snap;
    state[@"raster_snap"] = raster_snap;
    state[@"slots"] = slots;
    return state;
}

static void fail_executed(long long seq, NSString *op) __attribute__((noreturn));
static void fail_executed(long long seq, NSString *op) {
    g_expect = seq + 1;
    reply(@{
        @"schema": @"metal-shadow/v0",
        @"ok": @NO,
        @"seq": @(seq),
        @"next": @(g_expect),
        @"op": op,
        @"error": @"observation_failed",
        @"executed": @YES,
    });
    exit(1);
}

static void succeed(long long seq, NSString *op, NSDictionary *result) {
    NSDictionary *state = state_now();
    if (state == nil) {
        fail_executed(seq, op);
    }
    g_expect = seq + 1;
    reply(@{
        @"schema": @"metal-shadow/v0",
        @"ok": @YES,
        @"seq": @(seq),
        @"next": @(g_expect),
        @"op": op,
        @"result": result ?: @{},
        @"state": state,
    });
}

static NSString *require_up(void) {
    if (g_down) {
        return @"already_shutdown";
    }
    if (!g_up) {
        return @"not_initialized";
    }
    return nil;
}

static NSDictionary *header_view(void *header) {
    int32_t stride = load_i32(header, 0);
    int32_t count = load_i32(header, 4);
    uint32_t token = 0;
    memcpy(&token, (uint8_t *)header + 8, 4);
    return @{@"stride": @(stride), @"count": @(count), @"token": @(token)};
}

static NSString *lookup_buf(NSString *ident, ShadowBuf **out) {
    if (!valid_id(ident)) {
        return @"bad_id";
    }
    ShadowBuf *buf = g_live[ident];
    if (buf != nil) {
        *out = buf;
        return nil;
    }
    if ([g_ever containsObject:ident]) {
        return @"stale_resource";
    }
    return @"unknown_id";
}

static NSString *decode_exact(id value, size_t need, NSData **out) {
    if (value == nil) {
        return @"missing_field";
    }
    if (![value isKindOfClass:[NSString class]]) {
        return @"bad_type";
    }
    NSData *data = [[NSData alloc] initWithBase64EncodedString:value options:0];
    if (data == nil) {
        return @"bad_base64";
    }
    if (data.length != need) {
        return @"size";
    }
    *out = data;
    return nil;
}

static void op_settings(NSDictionary *args, long long seq) {
    int32_t display = 0, windowed = 0;
    if (!json_i32(args[@"display"], &display) || !json_i32(args[@"windowed"], &windowed)) {
        fail_sem(seq, @"settings", @"bad_type");
        return;
    }
    NSArray *samples = args[@"samples"];
    if (![samples isKindOfClass:[NSArray class]]) {
        fail_sem(seq, @"settings", samples == nil ? @"missing_field" : @"bad_type");
        return;
    }
    if (samples.count > 64) {
        fail_sem(seq, @"settings", @"bad_range");
        return;
    }
    uint8_t records[64 * 12];
    memset(records, 0, sizeof records);
    NSMutableArray *supports = [NSMutableArray arrayWithCapacity:samples.count];
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    for (NSUInteger i = 0; i < samples.count; i++) {
        uint32_t sample = 0;
        if (!json_u32(samples[i], &sample)) {
            fail_sem(seq, @"settings", @"bad_type");
            return;
        }
        memcpy(records + i * 12, &sample, 4);
        BOOL ok = device != nil && [device supportsTextureSampleCount:sample];
        [supports addObject:@(ok)];
    }
    uint8_t settings[0x28];
    memset(settings, 0, sizeof settings);
    uint8_t *array = samples.count > 0 ? records : NULL;
    memcpy(settings + 8, &array, sizeof array);
    int rc = eu4_gfx_settings(display, windowed, (int)samples.count, settings);
    NSMutableArray *flags = [NSMutableArray arrayWithCapacity:samples.count];
    for (NSUInteger i = 0; i < samples.count; i++) {
        [flags addObject:@(records[i * 12 + 4])];
    }
    uint32_t anisotropy = 0, constant = 0;
    uint64_t span = 0;
    memcpy(&anisotropy, settings + 0x14, 4);
    memcpy(&span, settings + 0x18, 8);
    memcpy(&constant, settings + 0x20, 4);
    succeed(seq, @"settings", @{
        @"return": @(rc),
        @"flags": flags,
        @"byte_10": @(settings[0x10]),
        @"anisotropy": @(anisotropy),
        @"span": @(span),
        @"constant": @(constant),
        @"device_supports": supports,
    });
}

static void op_configure(NSDictionary *args, long long seq) {
    if (g_did_init || g_up || g_down) {
        fail_sem(seq, @"configure_window", @"configure_after_init");
        return;
    }
    int32_t width = 0, height = 0;
    uint32_t window_id = 0, source = 0;
    if (!json_i32(args[@"width"], &width) || !json_i32(args[@"height"], &height) ||
        !json_u32(args[@"window_id"], &window_id) || !json_u32(args[@"source"], &source)) {
        fail_sem(seq, @"configure_window", @"bad_type");
        return;
    }
    g_width = width;
    g_height = height;
    g_window_id = window_id;
    g_source = source;
    succeed(seq, @"configure_window", @{
        @"width": @(width),
        @"height": @(height),
        @"window_id": @(window_id),
        @"source": @(source),
    });
}

static void op_init(NSDictionary *args, long long seq) {
    (void)args;
    if (g_did_init || g_up || g_down) {
        fail_sem(seq, @"init", @"already_initialized");
        return;
    }
    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    memcpy(handle, &g_window_id, 4);
    memcpy(handle + 0x18, &g_source, 4);
    uint8_t ok = 0;
    void *master = eu4_gfx_init(handle, &ok);
    void *token = NULL;
    if (master != NULL) {
        memcpy(&token, (uint8_t *)master + 0x180, sizeof token);
    }
    if (ok == 1 && master != NULL && g_slot == master && eu4_gfx_phase() == 2 && token != NULL) {
        g_master = master;
        g_up = 1;
        g_did_init = 1;
        succeed(seq, @"init", @{@"success": @1});
        return;
    }
    g_expect = seq + 1;
    reply(@{
        @"schema": @"metal-shadow/v0",
        @"ok": @NO,
        @"seq": @(seq),
        @"next": @(g_expect),
        @"op": @"init",
        @"error": @"init_failed",
    });
    exit(1);
}

static void op_state(NSDictionary *args, long long seq, NSString *op, void (*fn)(void *, void *)) {
    (void)args;
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, op, why);
        return;
    }
    fn(&g_master, NULL);
    succeed(seq, op, @{});
}

static void op_reset(NSDictionary *args, long long seq) {
    (void)args;
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"reset", why);
        return;
    }
    int rc = eu4_gfx_reset(g_master);
    succeed(seq, @"reset", @{@"return": @(rc)});
}

static void op_on_reset(NSDictionary *args, long long seq) {
    (void)args;
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"on_reset_master", why);
        return;
    }
    /* set_state loads the first qword. Init stores the self-pointer there. */
    eu4_gfx_on_reset_master(g_master);
    succeed(seq, @"on_reset_master", @{});
}

static void op_viewport(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"set_viewport", why);
        return;
    }
    NSArray *words = args[@"words"];
    if (![words isKindOfClass:[NSArray class]] || words.count != 6) {
        fail_sem(seq, @"set_viewport", @"bad_viewport");
        return;
    }
    int32_t rect[6];
    for (NSUInteger i = 0; i < 6; i++) {
        if (!json_i32(words[i], &rect[i])) {
            fail_sem(seq, @"set_viewport", @"bad_viewport");
            return;
        }
    }
    eu4_gfx_set_viewport(g_master, rect);
    succeed(seq, @"set_viewport", @{@"words": words});
}

static void op_create(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"create_vertex_buffer", why);
        return;
    }
    NSString *ident = args[@"resource_id"];
    if (ident == nil) {
        fail_sem(seq, @"create_vertex_buffer", @"missing_field");
        return;
    }
    if (!valid_id(ident)) {
        fail_sem(seq, @"create_vertex_buffer", @"bad_id");
        return;
    }
    if (g_live[ident] != nil) {
        fail_sem(seq, @"create_vertex_buffer", @"duplicate_id");
        return;
    }
    if ([g_ever containsObject:ident]) {
        fail_sem(seq, @"create_vertex_buffer", @"id_reused");
        return;
    }
    int32_t count = 0, stride = 0, usage = 0;
    if (!json_i32(args[@"count"], &count) || !json_i32(args[@"stride"], &stride) ||
        !json_i32(args[@"usage"], &usage)) {
        fail_sem(seq, @"create_vertex_buffer", @"bad_type");
        return;
    }
    size_t logical = 0;
    if (logical_size(count, stride, &logical) != 0) {
        fail_sem(seq, @"create_vertex_buffer", @"size");
        return;
    }
    id raw = args[@"data_base64"];
    if (raw == nil) {
        fail_sem(seq, @"create_vertex_buffer", @"missing_field");
        return;
    }
    NSData *bytes = nil;
    const void *data = NULL;
    BOOL known = NO;
    if ([raw isKindOfClass:[NSNull class]]) {
        known = NO;
    } else {
        NSString *size_error = decode_exact(raw, logical, &bytes);
        if (size_error != nil) {
            fail_sem(seq, @"create_vertex_buffer", size_error);
            return;
        }
        data = bytes.bytes;
        known = YES;
    }
    void *header = eu4_gfx_create_vertex_buffer(NULL, data, count, stride, usage);
    (void)bytes;
    if (header == NULL) {
        fail_sem(seq, @"create_vertex_buffer", @"create_failed");
        return;
    }
    ShadowBuf *buf = [ShadowBuf new];
    buf.header = header;
    buf.known = known;
    g_live[ident] = buf;
    [g_ever addObject:ident];
    size_t length = eu4_gfx_vertex_buffer_length(header);
    succeed(seq, @"create_vertex_buffer", @{
        @"resource_id": ident,
        @"header": header_view(header),
        @"contents_known": @(known),
        @"buffer": @{
            @"length": @(length == (size_t)-1 ? 0 : length),
            @"shared": @(eu4_gfx_vertex_buffer_shared(header) ? YES : NO),
        },
    });
}

static int effective_update(ShadowBuf *buf, int32_t count, size_t *logical) {
    int32_t stride = load_i32(buf.header, 0);
    int32_t used = count;
    if (used <= 0) {
        used = load_i32(buf.header, 4);
    }
    return logical_size(used, stride, logical);
}

static void op_update(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"update_vertex_buffer", why);
        return;
    }
    ShadowBuf *buf = nil;
    NSString *missing = lookup_buf(args[@"resource_id"], &buf);
    if (missing != nil) {
        fail_sem(seq, @"update_vertex_buffer", args[@"resource_id"] == nil ? @"missing_field" : missing);
        return;
    }
    int32_t count = 0, partial = 0;
    uint32_t offset = 0;
    if (!json_i32(args[@"count"], &count) || !json_i32(args[@"partial"], &partial) ||
        !json_u32(args[@"offset"], &offset)) {
        fail_sem(seq, @"update_vertex_buffer", @"bad_type");
        return;
    }
    if (partial != 0 && partial != 1) {
        fail_sem(seq, @"update_vertex_buffer", @"bad_partial");
        return;
    }
    size_t logical = 0;
    if (effective_update(buf, count, &logical) != 0) {
        fail_sem(seq, @"update_vertex_buffer", @"size");
        return;
    }
    NSData *bytes = nil;
    NSString *size_error = decode_exact(args[@"data_base64"], logical, &bytes);
    if (size_error != nil) {
        fail_sem(seq, @"update_vertex_buffer", size_error);
        return;
    }
    size_t length = eu4_gfx_vertex_buffer_length(buf.header);
    if (length == (size_t)-1) {
        fail_sem(seq, @"update_vertex_buffer", @"stale_resource");
        return;
    }
    if (partial == 1 && (offset > length || logical > length - offset)) {
        fail_sem(seq, @"update_vertex_buffer", @"bounds");
        return;
    }
    eu4_gfx_update_vertex_buffer(&g_master, buf.header, bytes.bytes, count, offset, partial);
    if (partial == 0) {
        buf.known = YES;
    }
    size_t now = eu4_gfx_vertex_buffer_length(buf.header);
    succeed(seq, @"update_vertex_buffer", @{
        @"resource_id": args[@"resource_id"],
        @"contents_known": @(buf.known),
        @"length": @(now == (size_t)-1 ? 0 : now),
    });
}

static void op_update_multiple(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"update_vertex_buffer_multiple", why);
        return;
    }
    ShadowBuf *buf = nil;
    NSString *missing = lookup_buf(args[@"resource_id"], &buf);
    if (missing != nil) {
        fail_sem(seq, @"update_vertex_buffer_multiple", args[@"resource_id"] == nil ? @"missing_field" : missing);
        return;
    }
    NSArray *ranges = args[@"ranges"];
    if (![ranges isKindOfClass:[NSArray class]]) {
        fail_sem(seq, @"update_vertex_buffer_multiple", ranges == nil ? @"missing_field" : @"bad_type");
        return;
    }
    if (ranges.count > 8) {
        fail_sem(seq, @"update_vertex_buffer_multiple", @"bad_range");
        return;
    }
    int32_t stride = load_i32(buf.header, 0);
    size_t length = eu4_gfx_vertex_buffer_length(buf.header);
    if (length == (size_t)-1) {
        fail_sem(seq, @"update_vertex_buffer_multiple", @"stale_resource");
        return;
    }
    NSMutableArray *kept = [NSMutableArray arrayWithCapacity:ranges.count];
    const void *ptrs[8];
    int32_t counts[8];
    size_t cursor = 0;
    for (NSUInteger i = 0; i < ranges.count; i++) {
        NSDictionary *range = ranges[i];
        if (![range isKindOfClass:[NSDictionary class]]) {
            fail_sem(seq, @"update_vertex_buffer_multiple", @"bad_type");
            return;
        }
        int32_t count = 0;
        if (!json_i32(range[@"count"], &count)) {
            fail_sem(seq, @"update_vertex_buffer_multiple", @"bad_type");
            return;
        }
        size_t logical = 0;
        if (logical_size(count, stride, &logical) != 0) {
            fail_sem(seq, @"update_vertex_buffer_multiple", @"size");
            return;
        }
        NSData *bytes = nil;
        NSString *size_error = decode_exact(range[@"data_base64"], logical, &bytes);
        if (size_error != nil) {
            fail_sem(seq, @"update_vertex_buffer_multiple", size_error);
            return;
        }
        if (logical > length - cursor) {
            fail_sem(seq, @"update_vertex_buffer_multiple", @"bounds");
            return;
        }
        cursor += logical;
        ptrs[i] = bytes.bytes;
        counts[i] = count;
        [kept addObject:bytes];
    }
    eu4_gfx_update_vertex_buffer_multiple(&g_master, buf.header, ptrs, counts, (int32_t)ranges.count);
    (void)kept;
    if (cursor == length) {
        /* shortcut: whole-buffer contents_known only, until a differential validator needs partial ranges. */
        buf.known = YES;
    }
    succeed(seq, @"update_vertex_buffer_multiple", @{
        @"resource_id": args[@"resource_id"],
        @"contents_known": @(buf.known),
        @"length": @(length),
    });
}

static void op_set_buffers(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"set_vertex_buffers", why);
        return;
    }
    int32_t start = 0;
    if (!json_i32(args[@"start"], &start)) {
        fail_sem(seq, @"set_vertex_buffers", @"bad_type");
        return;
    }
    NSArray *resources = args[@"resources"];
    if (![resources isKindOfClass:[NSArray class]]) {
        fail_sem(seq, @"set_vertex_buffers", resources == nil ? @"missing_field" : @"bad_type");
        return;
    }
    if (resources.count > EU4_GFX_VERTEX_SLOTS) {
        fail_sem(seq, @"set_vertex_buffers", @"bad_range");
        return;
    }
    int32_t count = (int32_t)resources.count;
    if (count > 0) {
        int64_t last = (int64_t)start + (int64_t)count - 1;
        if (start < 0 || last >= EU4_GFX_VERTEX_SLOTS) {
            fail_sem(seq, @"set_vertex_buffers", @"bad_range");
            return;
        }
    }
    void *headers[EU4_GFX_VERTEX_SLOTS];
    for (NSUInteger i = 0; i < resources.count; i++) {
        ShadowBuf *buf = nil;
        NSString *missing = lookup_buf(resources[i], &buf);
        if (missing != nil) {
            fail_sem(seq, @"set_vertex_buffers", missing);
            return;
        }
        headers[i] = buf.header;
    }
    eu4_gfx_set_vertex_buffers(&g_master, count, count > 0 ? headers : NULL, start, NULL, NULL);
    succeed(seq, @"set_vertex_buffers", @{@"start": @(start), @"count": @(count)});
}

static void op_destroy(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"destroy_vertex_buffer", why);
        return;
    }
    NSString *ident = args[@"resource_id"];
    ShadowBuf *buf = nil;
    NSString *missing = lookup_buf(ident, &buf);
    if (missing != nil) {
        fail_sem(seq, @"destroy_vertex_buffer", ident == nil ? @"missing_field" : missing);
        return;
    }
    eu4_gfx_destroy_vertex_buffer(buf.header);
    buf.header = NULL;
    [g_live removeObjectForKey:ident];
    succeed(seq, @"destroy_vertex_buffer", @{@"resource_id": ident});
}

static NSDictionary *bytes_view(void *header, BOOL known, int (*read)(void *, void *, size_t)) {
    size_t length = observed_length(header);
    if (length == (size_t)-1) {
        return nil;
    }
    NSMutableDictionary *view = [@{
        @"contents_known": @(known),
        @"length": @(length),
    } mutableCopy];
    if (!known) {
        return view;
    }
    NSMutableData *data = [NSMutableData dataWithLength:length];
    void *dest = length == 0 ? NULL : data.mutableBytes;
    if (read(header, dest, length) != 0) {
        return nil;
    }
    view[@"bytes_base64"] = [data base64EncodedStringWithOptions:0];
    return view;
}

static int read_header(void *header, void *dest, size_t n) {
    if (g_fail_read) {
        return -1;
    }
    return eu4_gfx_vertex_buffer_read(header, dest, n);
}

static int read_slot(int slot, void *dest, size_t n) {
    if (g_fail_read) {
        return -1;
    }
    return eu4_gfx_vertex_slot_read(slot, dest, n);
}

static void op_observe(NSDictionary *args, long long seq) {
    NSString *why = require_up();
    if (why != nil) {
        fail_sem(seq, @"observe", why);
        return;
    }
    NSArray *resources = args[@"resources"];
    NSArray *slots = args[@"slots"];
    if (resources != nil && ![resources isKindOfClass:[NSArray class]]) {
        fail_sem(seq, @"observe", @"bad_type");
        return;
    }
    if (slots != nil && ![slots isKindOfClass:[NSArray class]]) {
        fail_sem(seq, @"observe", @"bad_type");
        return;
    }
    NSMutableArray *resolved = [NSMutableArray array];
    if (resources != nil) {
        for (id ident in resources) {
            ShadowBuf *buf = nil;
            NSString *missing = lookup_buf(ident, &buf);
            if (missing != nil) {
                fail_sem(seq, @"observe", missing);
                return;
            }
            [resolved addObject:@[ident, buf]];
        }
    }
    NSMutableArray *slot_ids = [NSMutableArray array];
    if (slots != nil) {
        for (id value in slots) {
            int32_t slot = 0;
            if (!json_i32(value, &slot) || slot < 0 || slot >= EU4_GFX_VERTEX_SLOTS) {
                fail_sem(seq, @"observe", @"bad_range");
                return;
            }
            [slot_ids addObject:@(slot)];
        }
    }
    NSMutableDictionary *result = [NSMutableDictionary dictionary];
    result[@"live_ids"] = [g_live.allKeys sortedArrayUsingSelector:@selector(compare:)];
    if (resources != nil) {
        NSMutableDictionary *views = [NSMutableDictionary dictionary];
        for (NSArray *pair in resolved) {
            ShadowBuf *buf = pair[1];
            NSDictionary *buffer = bytes_view(buf.header, buf.known, read_header);
            if (buffer == nil) {
                fail_sem(seq, @"observe", @"observation_failed");
                return;
            }
            views[pair[0]] = @{
                @"header": header_view(buf.header),
                @"buffer": buffer,
            };
        }
        result[@"resources"] = views;
    }
    if (slots != nil) {
        NSMutableArray *views = [NSMutableArray array];
        for (NSNumber *number in slot_ids) {
            int slot = number.intValue;
            void *header = eu4_gfx_vertex_slot_header(slot);
            if (header == NULL) {
                [views addObject:@{
                    @"slot": @(slot),
                    @"resource_id": [NSNull null],
                    @"contents_known": @NO,
                    @"length": @0,
                }];
                continue;
            }
            size_t length = observed_length(header);
            NSString *ident = id_for_header(header);
            if (length == (size_t)-1 || ident == nil) {
                fail_sem(seq, @"observe", @"observation_failed");
                return;
            }
            ShadowBuf *buf = g_live[ident];
            BOOL known = buf != nil && buf.known;
            NSMutableDictionary *view = [@{
                @"slot": @(slot),
                @"resource_id": ident,
                @"contents_known": @(known),
                @"length": @(length),
            } mutableCopy];
            if (known) {
                NSMutableData *data = [NSMutableData dataWithLength:length];
                void *dest = length == 0 ? NULL : data.mutableBytes;
                if (read_slot(slot, dest, length) != 0) {
                    fail_sem(seq, @"observe", @"observation_failed");
                    return;
                }
                view[@"bytes_base64"] = [data base64EncodedStringWithOptions:0];
            }
            [views addObject:view];
        }
        result[@"slots"] = views;
    }
    succeed(seq, @"observe", result);
}

static void op_shutdown(NSDictionary *args, long long seq) {
    (void)args;
    if (g_down) {
        fail_sem(seq, @"shutdown", @"already_shutdown");
        return;
    }
    if (!g_up) {
        fail_sem(seq, @"shutdown", @"not_initialized");
        return;
    }
    if (g_live.count != 0) {
        fail_sem(seq, @"shutdown", @"live_resources");
        return;
    }
    eu4_gfx_shutdown(g_master);
    g_master = NULL;
    g_up = 0;
    g_down = 1;
    succeed(seq, @"shutdown", @{
        @"slot_null": @(g_slot == NULL ? YES : NO),
        @"initialized": @NO,
        @"backend_releases": @(eu4_gfx_backend_releases()),
    });
}

static void dispatch_op(NSString *op, NSDictionary *args, long long seq) {
    if ([op isEqual:@"settings"]) {
        op_settings(args, seq);
    } else if ([op isEqual:@"configure_window"]) {
        op_configure(args, seq);
    } else if ([op isEqual:@"init"]) {
        op_init(args, seq);
    } else if ([op isEqual:@"set_blend"]) {
        op_state(args, seq, op, eu4_gfx_set_blend);
    } else if ([op isEqual:@"set_depth"]) {
        op_state(args, seq, op, eu4_gfx_set_depth);
    } else if ([op isEqual:@"set_raster"]) {
        op_state(args, seq, op, eu4_gfx_set_raster);
    } else if ([op isEqual:@"reset"]) {
        op_reset(args, seq);
    } else if ([op isEqual:@"on_reset_master"]) {
        op_on_reset(args, seq);
    } else if ([op isEqual:@"set_viewport"]) {
        op_viewport(args, seq);
    } else if ([op isEqual:@"create_vertex_buffer"]) {
        op_create(args, seq);
    } else if ([op isEqual:@"update_vertex_buffer"]) {
        op_update(args, seq);
    } else if ([op isEqual:@"update_vertex_buffer_multiple"]) {
        op_update_multiple(args, seq);
    } else if ([op isEqual:@"set_vertex_buffers"]) {
        op_set_buffers(args, seq);
    } else if ([op isEqual:@"destroy_vertex_buffer"]) {
        op_destroy(args, seq);
    } else if ([op isEqual:@"observe"]) {
        op_observe(args, seq);
    } else if ([op isEqual:@"shutdown"]) {
        op_shutdown(args, seq);
    } else {
        fail_sem(seq, op, @"unknown_op");
    }
}

static void handle_line(char *line) {
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) {
        line[--n] = 0;
    }
    if (n == 0) {
        fail_env(@"malformed", nil);
        return;
    }
    NSData *data = [NSData dataWithBytes:line length:n];
    NSError *error = nil;
    id object = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![object isKindOfClass:[NSDictionary class]]) {
        fail_env(@"malformed", nil);
        return;
    }
    NSDictionary *msg = object;
    id schema = msg[@"schema"];
    if (![schema isKindOfClass:[NSString class]] || ![schema isEqual:@"metal-shadow/v0"]) {
        fail_env(@"bad_schema", nil);
        return;
    }
    long long seq = 0;
    if (!json_i64(msg[@"seq"], &seq)) {
        fail_env(@"bad_seq", nil);
        return;
    }
    if (seq != g_expect) {
        fail_env(@"bad_seq", @(seq));
        return;
    }
    id op = msg[@"op"];
    if (![op isKindOfClass:[NSString class]]) {
        fail_sem(seq, nil, @"bad_type");
        return;
    }
    id args = msg[@"args"];
    if (![args isKindOfClass:[NSDictionary class]]) {
        fail_sem(seq, op, @"bad_type");
        return;
    }
    dispatch_op(op, args, seq);
}

enum { kLineOK = 0, kLineEOF = 1, kLineLong = 2, kLineAlloc = 3, kLineIO = 4 };

static int read_line(char **out) {
    size_t cap = 4096;
    size_t n = 0;
    char *buf = (char *)malloc(cap);
    if (buf == NULL) {
        return kLineAlloc;
    }
    int c;
    while ((c = fgetc(stdin)) != EOF) {
        if (n + 1 >= cap) {
            if (cap >= kMaxLine) {
                free(buf);
                while (c != '\n' && c != EOF) {
                    c = fgetc(stdin);
                }
                if (ferror(stdin)) {
                    return kLineIO;
                }
                return kLineLong;
            }
            cap *= 2;
            char *grown = (char *)realloc(buf, cap);
            if (grown == NULL) {
                free(buf);
                return kLineAlloc;
            }
            buf = grown;
        }
        buf[n++] = (char)c;
        if (c == '\n') {
            break;
        }
    }
    if (ferror(stdin)) {
        free(buf);
        return kLineIO;
    }
    if (n == 0) {
        free(buf);
        return kLineEOF;
    }
    buf[n] = 0;
    *out = buf;
    return kLineOK;
}

static int boot(void) {
    size_t bytes = EU4_GFX_IB_CACHE_RVA + (size_t)EU4_GFX_IB_CACHE_SLOTS * sizeof(void *);
    g_image = (uint8_t *)mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (g_image == MAP_FAILED) {
        fprintf(stderr, "image map\n");
        return -1;
    }
    eu4_gfx_set_image(g_image, &g_slot);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fprintf(stderr, "no Metal device\n");
        return -1;
    }
    g_layer = [CAMetalLayer layer];
    g_layer.device = device;
    eu4_gfx_set_layer((__bridge void *)g_layer);
    char dir[] = "/tmp/eu4-shadow-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fprintf(stderr, "temp dir\n");
        return -1;
    }
    eu4_metal_events_set_dir(dir);
    fprintf(stderr, "receipt_dir %s\n", dir);
    const char *fail_read = getenv("EU4_SHADOW_FAIL_READ");
    g_fail_read = fail_read != NULL && strcmp(fail_read, "1") == 0;
    const char *fail_state = getenv("EU4_SHADOW_FAIL_STATE");
    if (fail_state != NULL && strcmp(fail_state, "length") == 0) {
        g_fail_state = 1;
    } else if (fail_state != NULL && strcmp(fail_state, "map") == 0) {
        g_fail_state = 2;
    }
    g_live = [NSMutableDictionary dictionary];
    g_ever = [NSMutableSet set];
    return 0;
}

int main(void) {
    @autoreleasepool {
        if (boot() != 0) {
            return 1;
        }
        for (;;) {
            @autoreleasepool {
                char *line = NULL;
                int kind = read_line(&line);
                if (kind == kLineEOF) {
                    break;
                }
                if (kind == kLineAlloc) {
                    fprintf(stderr, "read allocation\n");
                    return 1;
                }
                if (kind == kLineIO) {
                    fprintf(stderr, "read error\n");
                    return 1;
                }
                if (kind == kLineLong) {
                    fail_env(@"malformed", nil);
                    continue;
                }
                handle_line(line);
                free(line);
            }
        }
    }
    return 0;
}
