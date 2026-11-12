#import "eu4_gfx_shader.h"

/* The shader module is also linked into hosts that do not include the event
   writer. Diagnostics are best effort there; the engine boot always links it. */
extern "C" int eu4_metal_events_write(const char *name, const char *body)
    __attribute__((weak_import));

#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <new>
#import <pthread.h>
#import <string.h>
#import <stdlib.h>

extern "C" void *eu4_gfx_backend_token(void);

/* P5.2a target: gfx/FX/simple.lua technique Simple (asset-maincode-v0).
   Metal translation of VertexShaderSimple / PixelShaderSimple. Not Color. */

static const char kSimpleMSL[] = R"(
#include <metal_stdlib>
using namespace metal;

struct VSIn {
    /* Authentic Simple vertex buffer: stride 24, float3 position at +0,
       float2 texcoord at +12, normalized uchar4 color at +20
       (L3.2b-I packet payloads/vb_3.bin).  The GLSL declares float4 but the
       engine binds three components for POSITION, so w is implicit 1. */
    float3 position [[attribute(0)]];
    float2 texcoord [[attribute(1)]];
    float4 color    [[attribute(2)]];
};

struct VSOut {
    float4 position [[position]];
    float2 texcoord;
    float4 color;
};

struct Uniforms {
    float4 UNIFORM0[4];
};

vertex VSOut simple_vs(VSIn in [[stage_in]],
                       constant Uniforms &u [[buffer(1)]]) {
    float4x4 Matrix = float4x4(u.UNIFORM0[0], u.UNIFORM0[1],
                               u.UNIFORM0[2], u.UNIFORM0[3]);
    VSOut out;
    out.position = Matrix * float4(in.position, 1.0);
    out.texcoord = in.texcoord;
    out.color = in.color;
    return out;
}

fragment float4 simple_fs(VSOut in [[stage_in]],
                          texture2d<float> SimpleTexture [[texture(0)]],
                          sampler SimpleSampler [[sampler(0)]]) {
    float4 tex = SimpleTexture.sample(SimpleSampler, in.texcoord);
    return tex * in.color;
}
)";

static const char kSpriteMSL[] = R"(
#include <metal_stdlib>
using namespace metal;

struct VSIn {
    /* Live sprite/font vertex buffer: stride 20, float3 position at +0,
       float2 texcoord at +12. No per-vertex colour. */
    float3 position [[attribute(0)]];
    float2 texcoord [[attribute(1)]];
};

struct VSOut {
    float4 position [[position]];
    float2 texcoord;
};

struct Uniforms {
    float4 UNIFORM0[6];
};

vertex VSOut sprite_vs(VSIn in [[stage_in]],
                       constant Uniforms &u [[buffer(1)]]) {
    float4x4 Matrix = float4x4(u.UNIFORM0[0], u.UNIFORM0[1],
                               u.UNIFORM0[2], u.UNIFORM0[3]);
    VSOut out;
    out.position = Matrix * float4(in.position, 1.0);
    out.texcoord = in.texcoord;
    out.texcoord.x += u.UNIFORM0[5].x;
    return out;
}

fragment float4 sprite_fs(VSOut in [[stage_in]],
                          constant Uniforms &u [[buffer(1)]],
                          texture2d<float> MapTexture [[texture(0)]],
                          sampler MapSampler [[sampler(0)]]) {
    return MapTexture.sample(MapSampler, in.texcoord) * u.UNIFORM0[4];
}
)";

static const char kSimpleVertexSource[] =
    EU4_GFX_SHADER_SIMPLE_VS_MARKER "\n"
    "/* asset-maincode-v0 VertexShaderSimple from gfx/FX/simple.shader */\n"
    "VS_OUTPUT main(const VS_INPUT v)\n"
    "{\n"
    "    VS_OUTPUT Out;\n"
    "    Out.vPosition = mul( Matrix, v.vPosition );\n"
    "    Out.vTexCoord = v.vTexCoord;\n"
    "    Out.vColor = v.vColor;\n"
    "    return Out;\n"
    "}\n";

static const char kSimplePixelSource[] =
    EU4_GFX_SHADER_SIMPLE_PS_MARKER "\n"
    "/* asset-maincode-v0 PixelShaderSimple from gfx/FX/simple.shader */\n"
    "float4 main( VS_OUTPUT v ) : PDX_COLOR\n"
    "{\n"
    "    float4 OutColor = tex2D( SimpleTexture, v.vTexCoord );\n"
    "    OutColor = OutColor * v.vColor;\n"
    "    return OutColor;\n"
    "}\n";

@interface EU4ShaderRecord : NSObject
@property (nonatomic) uint64_t lifetimeId;
@property (nonatomic) void *engineObject;
@property (nonatomic) BOOL hasVertex;
@property (nonatomic) BOOL hasPixel;
@property (nonatomic) BOOL linked;
@property (nonatomic, copy) NSString *identity;
@property (nonatomic, strong) id<MTLLibrary> library;
@property (nonatomic, strong) id<MTLFunction> vertexFunction;
@property (nonatomic, strong) id<MTLFunction> fragmentFunction;
@property (nonatomic, strong) NSMutableDictionary *pipelines; /* key string -> PSO */
@end

@implementation EU4ShaderRecord
@end

static NSMutableArray<EU4ShaderRecord *> *g_shaders;
static uint64_t g_next_lifetime = 1;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static id<MTLDevice> g_device;
typedef struct {
    uint8_t digest[32];
    int stage;
    char effect[64];
} EU4QualifiedSource;

static EU4QualifiedSource g_qualified[EU4_GFX_SHADER_MAX_QUALIFIED_HASHES];
static size_t g_qualified_count;
/* Off by default. Offline hosts may opt in; engine-path completion must not. */
static int g_allow_transitional_markers = 0;

static void sha256_bytes(const void *data, size_t len, uint8_t out[32]) {
    CC_SHA256(data, (CC_LONG)len, out);
}

static const char *effect_known(const char *effect) {
    if (effect == NULL) {
        return NULL;
    }
    if (strcmp(effect, EU4_GFX_SHADER_SIMPLE_IDENTITY) == 0) {
        return EU4_GFX_SHADER_SIMPLE_IDENTITY;
    }
    if (strcmp(effect, EU4_GFX_SHADER_SPRITE_IDENTITY) == 0) {
        return EU4_GFX_SHADER_SPRITE_IDENTITY;
    }
    return NULL;
}

static const char *qualified_effect(const uint8_t digest[32], int stage) {
    for (size_t i = 0; i < g_qualified_count; i++) {
        if (g_qualified[i].stage == stage &&
            memcmp(g_qualified[i].digest, digest, 32) == 0) {
            return g_qualified[i].effect;
        }
    }
    return NULL;
}

static int hash_is_qualified(const uint8_t digest[32], int stage) {
    return qualified_effect(digest, stage) != NULL;
}

int eu4_gfx_shader_register_qualified_sha256(int stage, const uint8_t digest[32],
                                             const char *effect_identity) {
    const char *effect = effect_known(effect_identity);
    if (digest == NULL || effect == NULL ||
        (stage != EU4_GFX_SHADER_STAGE_VERTEX && stage != EU4_GFX_SHADER_STAGE_PIXEL)) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    if (hash_is_qualified(digest, stage)) {
        pthread_mutex_unlock(&g_lock);
        return 1;
    }
    if (g_qualified_count >= EU4_GFX_SHADER_MAX_QUALIFIED_HASHES) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    memcpy(g_qualified[g_qualified_count].digest, digest, 32);
    g_qualified[g_qualified_count].stage = stage;
    snprintf(g_qualified[g_qualified_count].effect,
             sizeof g_qualified[g_qualified_count].effect, "%s", effect);
    g_qualified_count++;
    pthread_mutex_unlock(&g_lock);
    return 1;
}

size_t eu4_gfx_shader_qualified_count(void) {
    pthread_mutex_lock(&g_lock);
    size_t count = g_qualified_count;
    pthread_mutex_unlock(&g_lock);
    return count;
}

static int hex_digest(NSString *text, uint8_t out[32]) {
    if (text == nil || text.length != 64) {
        return 0;
    }
    const char *bytes = text.UTF8String;
    for (int i = 0; i < 32; i++) {
        char pair[3] = {bytes[i * 2], bytes[i * 2 + 1], 0};
        char *end = NULL;
        unsigned long value = strtoul(pair, &end, 16);
        if (end == pair || *end != 0) {
            return 0;
        }
        out[i] = (uint8_t)value;
    }
    return 1;
}

int eu4_gfx_shader_load_qualified_sources(const char *path) {
    if (path == NULL || path[0] != '/') {
        return -1;
    }
    NSData *data = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:path]];
    if (data == nil) {
        return -1;
    }
    NSError *error = nil;
    id root = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![root isKindOfClass:[NSDictionary class]]) {
        return -1;
    }
    NSArray *sources = ((NSDictionary *)root)[@"sources"];
    if (![sources isKindOfClass:[NSArray class]]) {
        return -1;
    }
    if (sources.count > EU4_GFX_SHADER_MAX_QUALIFIED_HASHES) {
        return -1;
    }
    EU4QualifiedSource staged[EU4_GFX_SHADER_MAX_QUALIFIED_HASHES];
    memset(staged, 0, sizeof staged);
    size_t registered = 0;
    for (id entry in sources) {
        if (![entry isKindOfClass:[NSDictionary class]]) {
            return -1;
        }
        NSDictionary *record = (NSDictionary *)entry;
        NSString *stage = record[@"stage"];
        NSString *effect = record[@"effect"];
        uint8_t digest[32];
        if (![stage isKindOfClass:[NSString class]] ||
            ![effect isKindOfClass:[NSString class]] ||
            effect_known(effect.UTF8String) == NULL ||
            !hex_digest(record[@"sha256"], digest)) {
            return -1;
        }
        int stage_value = 0;
        if ([stage isEqualToString:@"vertex"]) {
            stage_value = EU4_GFX_SHADER_STAGE_VERTEX;
        } else if ([stage isEqualToString:@"pixel"]) {
            stage_value = EU4_GFX_SHADER_STAGE_PIXEL;
        } else {
            return -1;
        }
        memcpy(staged[registered].digest, digest, 32);
        staged[registered].stage = stage_value;
        snprintf(staged[registered].effect,
                 sizeof staged[registered].effect, "%s", effect.UTF8String);
        registered += 1;
    }
    /* Validate every record before changing the active set. */
    pthread_mutex_lock(&g_lock);
    memset(g_qualified, 0, sizeof g_qualified);
    memcpy(g_qualified, staged, sizeof staged);
    g_qualified_count = registered;
    pthread_mutex_unlock(&g_lock);
    return (int)registered;
}

void eu4_gfx_shader_clear_qualified_hashes(void) {
    pthread_mutex_lock(&g_lock);
    g_qualified_count = 0;
    memset(g_qualified, 0, sizeof g_qualified);
    pthread_mutex_unlock(&g_lock);
}

static const char *admit_source(const char *source, int stage,
                                int (*marker_fn)(const char *)) {
    if (source == NULL) {
        return NULL;
    }
    uint8_t digest[32];
    sha256_bytes(source, strlen(source), digest);
    const char *effect = qualified_effect(digest, stage);
    if (effect != NULL) {
        return effect;
    }
    /* Transitional offline path only — not engine-path completion. */
    if (g_allow_transitional_markers && marker_fn(source)) {
        return EU4_GFX_SHADER_SIMPLE_IDENTITY;
    }
    return NULL;
}

static int source_admitted(const char *source, int stage,
                           int (*marker_fn)(const char *))
    __attribute__((unused));
static int source_admitted(const char *source, int stage,
                           int (*marker_fn)(const char *)) {
    return admit_source(source, stage, marker_fn) != NULL;
}

static void ensure_registry(void) {
    if (g_shaders == nil) {
        g_shaders = [NSMutableArray array];
    }
}

static EU4ShaderRecord *find_record(const void *object) {
    ensure_registry();
    for (EU4ShaderRecord *record in g_shaders) {
        if (record.engineObject == object) {
            return record;
        }
    }
    return nil;
}

static id<MTLDevice> metal_device(void) {
    if (g_device != nil) {
        return g_device;
    }
    void *token = eu4_gfx_backend_token();
    if (token != NULL) {
        id backend = (__bridge id)token;
        id device = [backend valueForKey:@"device"];
        if (device != nil) {
            g_device = device;
            return g_device;
        }
    }
    g_device = MTLCreateSystemDefaultDevice();
    return g_device;
}

static int source_is_simple_vertex(const char *source) {
    if (source == NULL) {
        return 0;
    }
    if (strstr(source, EU4_GFX_SHADER_SIMPLE_VS_MARKER) != NULL) {
        return 1;
    }
    /* Surviving markers from the asset MainCode / BuildGLSL expansion. */
    return strstr(source, "Out.vPosition") != NULL &&
           strstr(source, "v.vTexCoord") != NULL &&
           strstr(source, "v.vColor") != NULL &&
           strstr(source, "mul") != NULL;
}

static int source_is_simple_pixel(const char *source) {
    if (source == NULL) {
        return 0;
    }
    if (strstr(source, EU4_GFX_SHADER_SIMPLE_PS_MARKER) != NULL) {
        return 1;
    }
    return strstr(source, "SimpleTexture") != NULL &&
           strstr(source, "v.vTexCoord") != NULL &&
           strstr(source, "v.vColor") != NULL;
}

static int compile_library(EU4ShaderRecord *record, const char *effect) {
    id<MTLDevice> device = metal_device();
    if (device == nil) {
        return 0;
    }
    NSError *error = nil;
    NSString *source;
    const char *vs_name;
    const char *fs_name;
    if (strcmp(effect, EU4_GFX_SHADER_SPRITE_IDENTITY) == 0) {
        source = [[NSString alloc] initWithUTF8String:kSpriteMSL];
        vs_name = "sprite_vs";
        fs_name = "sprite_fs";
    } else if (strcmp(effect, EU4_GFX_SHADER_SIMPLE_IDENTITY) == 0) {
        source = [[NSString alloc] initWithUTF8String:kSimpleMSL];
        vs_name = "simple_vs";
        fs_name = "simple_fs";
    } else {
        return 0;
    }
    id<MTLLibrary> library =
        [device newLibraryWithSource:source options:nil error:&error];
    if (library == nil) {
        return 0;
    }
    id<MTLFunction> vs = [library newFunctionWithName:
                                   [NSString stringWithUTF8String:vs_name]];
    id<MTLFunction> fs = [library newFunctionWithName:
                                   [NSString stringWithUTF8String:fs_name]];
    if (vs == nil || fs == nil) {
        return 0;
    }
    record.library = library;
    record.vertexFunction = vs;
    record.fragmentFunction = fs;
    record.identity = [NSString stringWithUTF8String:effect];
    return 1;
}

const char *eu4_gfx_simple_vertex_source(void) { return kSimpleVertexSource; }
const char *eu4_gfx_simple_pixel_source(void) { return kSimplePixelSource; }

void eu4_gfx_shader_set_allow_transitional_markers(int allowed) {
    pthread_mutex_lock(&g_lock);
    g_allow_transitional_markers = allowed ? 1 : 0;
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_shader_test_reset(void) {
    pthread_mutex_lock(&g_lock);
    [g_shaders removeAllObjects];
    g_next_lifetime = 1;
    g_device = nil;
    g_qualified_count = 0;
    memset(g_qualified, 0, sizeof g_qualified);
    /* Offline host helper: opt into transitional marker admission for unit tests.
       Engine path never calls this; default remains off. */
    g_allow_transitional_markers = 1;
    pthread_mutex_unlock(&g_lock);
}

int eu4_gfx_shader_test_compile_identity(void *shader, const char *identity) {
    if (shader == NULL || effect_known(identity) == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    record.vertexFunction = nil;
    record.fragmentFunction = nil;
    record.library = nil;
    if (!compile_library(record, identity)) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    record.hasVertex = YES;
    record.hasPixel = YES;
    record.linked = YES;
    if (record.pipelines == nil) {
        record.pipelines = [NSMutableDictionary dictionary];
    } else {
        [record.pipelines removeAllObjects];
    }
    *reinterpret_cast<uint32_t *>(shader) = 0;
    pthread_mutex_unlock(&g_lock);
    return 1;
}

int eu4_gfx_shader_test_set_identity(void *shader, const char *identity) {
    if (shader == NULL || identity == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    int ok = 0;
    if (record != nil) {
        record.identity = [NSString stringWithUTF8String:identity];
        ok = record.identity != nil;
    }
    pthread_mutex_unlock(&g_lock);
    return ok;
}

uint64_t eu4_gfx_shader_lifetime_id(const void *shader) {
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    uint64_t id = record != nil ? record.lifetimeId : 0;
    pthread_mutex_unlock(&g_lock);
    return id;
}

int eu4_gfx_shader_is_linked(const void *shader) {
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    int linked = record != nil && record.linked ? 1 : 0;
    pthread_mutex_unlock(&g_lock);
    return linked;
}

const char *eu4_gfx_shader_identity(const void *shader) {
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    const char *identity =
        record != nil && record.identity != nil ? record.identity.UTF8String : NULL;
    pthread_mutex_unlock(&g_lock);
    return identity;
}

static void sha256_hex(const char *source, char out[65]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(source, (CC_LONG)strlen(source), digest);
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = digits[digest[i] >> 4];
        out[i * 2 + 1] = digits[digest[i] & 0xf];
    }
    out[64] = 0;
}

static int g_shader_event_seq;

/* Diagnostic only: preserve the exact compiler input a stage replacement saw.
 * The file name carries the source digest so it correlates with the shader
 * event without a second sequence. First-occurrence wins (bounded by the event
 * cap), and the writer is absent on hosts that do not link the event module. */
static void write_shader_source(const char *kind, const char *source) {
    if (eu4_metal_events_write == NULL || source == NULL || kind == NULL) {
        return;
    }
    char digest[65];
    sha256_hex(source, digest);
    char name[160];
    snprintf(name, sizeof name, "shader-source-%s-%s.glsl", kind, digest);
    (void)eu4_metal_events_write(name, source);
}

static void write_shader_event(const char *kind, const char *body) {
    if (eu4_metal_events_write == NULL) {
        return;
    }
    /* Bounded, append-only diagnostic names: the first occurrence rule would
       hide later shader objects, and the Simple effect is not necessarily the
       first shader the engine creates. */
    if (g_shader_event_seq >= 128) {
        return;
    }
    g_shader_event_seq += 1;
    char name[96];
    snprintf(name, sizeof name, "shader-%s-%03d.json", kind, g_shader_event_seq);
    (void)eu4_metal_events_write(name, body);
}

void *eu4_gfx_create_shader(void) {
    auto *object = static_cast<uint8_t *>(::operator new(EU4_GFX_SHADER_OBJECT_SIZE));
    memset(object, 0, EU4_GFX_SHADER_OBJECT_SIZE);

    EU4ShaderRecord *record = [EU4ShaderRecord new];
    pthread_mutex_lock(&g_lock);
    ensure_registry();
    record.lifetimeId = g_next_lifetime++;
    record.engineObject = object;
    [g_shaders addObject:record];
    pthread_mutex_unlock(&g_lock);
    return object;
}

int eu4_gfx_add_vertex_shader(void *unused, void *shader, const char *source) {
    (void)unused;
    if (shader == NULL || source == NULL) {
        return 0;
    }
    write_shader_source("vs", source);

    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil) {
        /* Address reuse without create: reject rather than fabricate. */
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    const char *effect = admit_source(source, EU4_GFX_SHADER_STAGE_VERTEX,
                                      source_is_simple_vertex);
    if (effect == NULL ||
        (record.identity != nil &&
         strcmp(record.identity.UTF8String, effect) != 0)) {
        /* Rejected replacement: invalidate prior link / PSO observations. */
        record.linked = NO;
        record.hasVertex = NO;
        record.vertexFunction = nil;
        [record.pipelines removeAllObjects];
        pthread_mutex_unlock(&g_lock);
        char digest[65];
        sha256_hex(source, digest);
        char event[512];
        snprintf(event, sizeof event,
                 "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"add_vertex\","
                 "\"shader\":\"0x%llx\",\"accepted\":false,\"source_sha256\":\"%s\","
                 "\"source_length\":%zu,\"identity\":\"%s\",\"pid\":%d}\n",
                 (unsigned long long)(uintptr_t)shader, digest, strlen(source),
                 eu4_gfx_shader_identity(shader) ?: "", (int)getpid());
        write_shader_event("vs", event);
        return 0;
    }
    if (record.library == nil || record.vertexFunction == nil ||
        record.fragmentFunction == nil || record.identity == nil ||
        strcmp(record.identity.UTF8String, effect) != 0) {
        record.vertexFunction = nil;
        record.fragmentFunction = nil;
        record.library = nil;
        if (!compile_library(record, effect)) {
            record.linked = NO;
            pthread_mutex_unlock(&g_lock);
            return 0;
        }
    }
    if (record.vertexFunction == nil) {
        record.linked = NO;
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    record.hasVertex = YES;
    record.linked = NO;
    if (record.pipelines == nil) {
        record.pipelines = [NSMutableDictionary dictionary];
    } else {
        [record.pipelines removeAllObjects];
    }
    /* Keep +4 as zero: not a GL name and not a Metal handle. */
    memset(static_cast<uint8_t *>(shader) + 4, 0, 4);
    pthread_mutex_unlock(&g_lock);
    char digest[65];
    sha256_hex(source, digest);
    char event[512];
    snprintf(event, sizeof event,
             "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"add_vertex\","
             "\"shader\":\"0x%llx\",\"accepted\":true,\"source_sha256\":\"%s\","
             "\"source_length\":%zu,\"identity\":\"%s\",\"pid\":%d}\n",
             (unsigned long long)(uintptr_t)shader, digest, strlen(source),
             eu4_gfx_shader_identity(shader) ?: "", (int)getpid());
    write_shader_event("vs", event);
    return 1;
}

int eu4_gfx_add_pixel_shader(void *unused, void *shader, const char *source) {
    (void)unused;
    if (shader == NULL || source == NULL) {
        return 0;
    }
    write_shader_source("ps", source);

    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    const char *effect = admit_source(source, EU4_GFX_SHADER_STAGE_PIXEL,
                                      source_is_simple_pixel);
    if (effect == NULL ||
        (record.identity != nil &&
         strcmp(record.identity.UTF8String, effect) != 0)) {
        record.linked = NO;
        record.hasPixel = NO;
        record.fragmentFunction = nil;
        [record.pipelines removeAllObjects];
        pthread_mutex_unlock(&g_lock);
        char digest[65];
        sha256_hex(source, digest);
        char event[512];
        snprintf(event, sizeof event,
                 "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"add_pixel\","
                 "\"shader\":\"0x%llx\",\"accepted\":false,\"source_sha256\":\"%s\","
                 "\"source_length\":%zu,\"identity\":\"%s\",\"pid\":%d}\n",
                 (unsigned long long)(uintptr_t)shader, digest, strlen(source),
                 eu4_gfx_shader_identity(shader) ?: "", (int)getpid());
        write_shader_event("ps", event);
        return 0;
    }
    if (record.library == nil || record.vertexFunction == nil ||
        record.fragmentFunction == nil || record.identity == nil ||
        strcmp(record.identity.UTF8String, effect) != 0) {
        record.vertexFunction = nil;
        record.fragmentFunction = nil;
        record.library = nil;
        if (!compile_library(record, effect)) {
            record.linked = NO;
            pthread_mutex_unlock(&g_lock);
            return 0;
        }
    }
    if (record.fragmentFunction == nil) {
        record.linked = NO;
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    record.hasPixel = YES;
    record.linked = NO;
    if (record.pipelines == nil) {
        record.pipelines = [NSMutableDictionary dictionary];
    } else {
        [record.pipelines removeAllObjects];
    }
    memset(static_cast<uint8_t *>(shader) + 8, 0, 4);
    pthread_mutex_unlock(&g_lock);
    char digest[65];
    sha256_hex(source, digest);
    char event[512];
    snprintf(event, sizeof event,
             "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"add_pixel\","
             "\"shader\":\"0x%llx\",\"accepted\":true,\"source_sha256\":\"%s\","
             "\"source_length\":%zu,\"identity\":\"%s\",\"pid\":%d}\n",
             (unsigned long long)(uintptr_t)shader, digest, strlen(source),
             eu4_gfx_shader_identity(shader) ?: "", (int)getpid());
    write_shader_event("ps", event);
    return 1;
}

int eu4_gfx_link_shader(void *unused, void *shader) {
    (void)unused;
    if (shader == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil || !record.hasVertex || !record.hasPixel ||
        record.vertexFunction == nil || record.fragmentFunction == nil) {
        if (record != nil) {
            record.linked = NO;
        }
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    record.linked = YES;
    /* Program name field stays 0 — not a fabricated GL name. */
    *reinterpret_cast<uint32_t *>(shader) = 0;
    pthread_mutex_unlock(&g_lock);
    char event[256];
    snprintf(event, sizeof event,
             "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"link\","
             "\"shader\":\"0x%llx\",\"accepted\":true,\"identity\":\"%s\",\"pid\":%d}\n",
             (unsigned long long)(uintptr_t)shader, eu4_gfx_shader_identity(shader) ?: "",
             (int)getpid());
    write_shader_event("link", event);
    return 1;
}

void eu4_gfx_set_shader(void *immediate_context, void *shader) {
    if (shader == NULL) {
        return;
    }
    if (immediate_context == NULL) {
        return;
    }
    void **context = static_cast<void **>(immediate_context);
    uint8_t *master = static_cast<uint8_t *>(context[0]);
    if (master == NULL) {
        return;
    }
    void **current = reinterpret_cast<void **>(master + 0x70);
    if (*current == shader) {
        return;
    }

    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    int known = record != nil;
    int linked = record != nil && record.linked;
    pthread_mutex_unlock(&g_lock);
    if (!known) {
        /* An object the replacement did not create is never published. */
        return;
    }

    /* The original engine may select a shader before linking it. Publishing the
       pointer preserves that ordering; the draw encoder still requires a
       linked shader and fails closed if the link never happens. */
    *current = shader;
    master[0x128] = 1;
    master[0x170] = 1;
    char event[384];
    snprintf(event, sizeof event,
             "{\"schema\":\"gfx-shader-event/v1\",\"event\":\"set\","
             "\"shader\":\"0x%llx\",\"linked\":%s,\"identity\":\"%s\","
             "\"selected\":\"%s\",\"pid\":%d}\n",
             (unsigned long long)(uintptr_t)shader, linked ? "true" : "false",
             eu4_gfx_shader_identity(shader) ?: "",
             eu4_gfx_shader_identity(shader) ?: "", (int)getpid());
    write_shader_event("set", event);
}

void eu4_gfx_destroy_shader(void *shader) {
    if (shader == NULL) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record != nil) {
        [g_shaders removeObject:record];
    }
    pthread_mutex_unlock(&g_lock);
    ::operator delete(shader);
}

int eu4_gfx_shader_is_current(const void *immediate_context, const void *shader) {
    if (immediate_context == NULL || shader == NULL) {
        return 0;
    }
    void *const *context = static_cast<void *const *>(immediate_context);
    const uint8_t *master = static_cast<const uint8_t *>(context[0]);
    if (master == NULL) {
        return 0;
    }
    void *const *current = reinterpret_cast<void *const *>(master + 0x70);
    return *current == shader ? 1 : 0;
}

static NSString *pipeline_cache_key(const EU4GfxPipelineKey *key) {
    return [NSString stringWithFormat:@"%p|%p|%p|%u|%u|%u|%u|%u", key->mtl_device,
                     key->vertex_function, key->fragment_function, key->pixel_format,
                     key->sample_count, key->color_write_mask, key->blend_enabled,
                     key->vertex_layout];
}

int eu4_gfx_shader_reflect_validate(const void *shader) {
    if (shader == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil || !record.linked || record.vertexFunction == nil ||
        record.fragmentFunction == nil) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    id<MTLFunction> vs = record.vertexFunction;
    id<MTLFunction> fs = record.fragmentFunction;
    NSArray<MTLVertexAttribute *> *attrs = vs.vertexAttributes;
    NSUInteger attr_count = attrs != nil ? attrs.count : 0;
    int simple_names = [vs.name isEqualToString:@"simple_vs"] &&
                       [fs.name isEqualToString:@"simple_fs"];
    int sprite_names = [vs.name isEqualToString:@"sprite_vs"] &&
                       [fs.name isEqualToString:@"sprite_fs"];
    /* Prefer Metal reflection (2+ vertex attrs); fall back to the known stage
       names when the runtime omits attribute metadata for stage_in. */
    int ok = vs.name.length > 0 && fs.name.length > 0;
    if (attr_count >= 2) {
        ok = ok && (simple_names || sprite_names);
    } else if (!simple_names && !sprite_names) {
        ok = 0;
    }
    pthread_mutex_unlock(&g_lock);
    return ok ? 1 : 0;
}

void *eu4_gfx_shader_pipeline_for_key(const void *shader, void *mtl_device,
                                      const EU4GfxPipelineKey *key) {
    if (shader == NULL || key == NULL) {
        return NULL;
    }
    id<MTLDevice> device =
        mtl_device != NULL ? (__bridge id<MTLDevice>)mtl_device : metal_device();
    if (device == nil) {
        return NULL;
    }

    pthread_mutex_lock(&g_lock);
    EU4ShaderRecord *record = find_record(shader);
    if (record == nil || !record.linked || record.vertexFunction == nil ||
        record.fragmentFunction == nil) {
        pthread_mutex_unlock(&g_lock);
        return NULL;
    }
    if (record.pipelines == nil) {
        record.pipelines = [NSMutableDictionary dictionary];
    }
    EU4GfxPipelineKey effective = *key;
    if (effective.mtl_device == NULL) {
        effective.mtl_device = (__bridge void *)device;
    } else if (effective.mtl_device != (__bridge void *)device) {
        /* Key device disagrees with the construction device — refuse. */
        pthread_mutex_unlock(&g_lock);
        return NULL;
    }
    if (effective.vertex_function == NULL) {
        effective.vertex_function = (__bridge void *)record.vertexFunction;
    }
    if (effective.fragment_function == NULL) {
        effective.fragment_function = (__bridge void *)record.fragmentFunction;
    }
    NSString *cacheKey = pipeline_cache_key(&effective);
    id<MTLRenderPipelineState> cached = record.pipelines[cacheKey];
    if (cached != nil) {
        void *out = (__bridge void *)cached;
        pthread_mutex_unlock(&g_lock);
        return out;
    }

    MTLVertexDescriptor *vertex = [[MTLVertexDescriptor alloc] init];
    if (effective.vertex_layout == EU4_GFX_VERTEX_LAYOUT_SPRITE_MAP) {
        /* Live sprite/font layout: stride 20, float3 POSITION at +0,
           float2 TEXCOORD0 at +12, no per-vertex colour. */
        vertex.attributes[0].format = MTLVertexFormatFloat3;
        vertex.attributes[0].offset = 0;
        vertex.attributes[0].bufferIndex = 0;
        vertex.attributes[1].format = MTLVertexFormatFloat2;
        vertex.attributes[1].offset = 12;
        vertex.attributes[1].bufferIndex = 0;
        vertex.layouts[0].stride = 20;
        vertex.layouts[0].stepFunction = MTLVertexStepFunctionPerVertex;
    } else {
        /* Authentic Simple layout from the L3.2b-I draw: stride 24, POSITION is
           three floats, TEXCOORD0 two floats, PDX_COLOR four normalized bytes.
           A float4 POSITION here would consume the texcoord and corrupt the
           quad. */
        vertex.attributes[0].format = MTLVertexFormatFloat3;
        vertex.attributes[0].offset = 0;
        vertex.attributes[0].bufferIndex = 0;
        vertex.attributes[1].format = MTLVertexFormatFloat2;
        vertex.attributes[1].offset = 12;
        vertex.attributes[1].bufferIndex = 0;
        vertex.attributes[2].format = MTLVertexFormatUChar4Normalized;
        vertex.attributes[2].offset = 20;
        vertex.attributes[2].bufferIndex = 0;
        vertex.layouts[0].stride = 24;
        vertex.layouts[0].stepFunction = MTLVertexStepFunctionPerVertex;
    }

    MTLRenderPipelineDescriptor *desc = [[MTLRenderPipelineDescriptor alloc] init];
    desc.vertexFunction = (__bridge id<MTLFunction>)effective.vertex_function;
    desc.fragmentFunction = (__bridge id<MTLFunction>)effective.fragment_function;
    desc.vertexDescriptor = vertex;
    desc.colorAttachments[0].pixelFormat =
        (MTLPixelFormat)(effective.pixel_format ? effective.pixel_format
                                                : MTLPixelFormatBGRA8Unorm);
    desc.rasterSampleCount = effective.sample_count ? effective.sample_count : 1;
    desc.colorAttachments[0].blendingEnabled = effective.blend_enabled ? YES : NO;
    desc.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
    desc.colorAttachments[0].destinationRGBBlendFactor =
        MTLBlendFactorOneMinusSourceAlpha;
    desc.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
    desc.colorAttachments[0].destinationAlphaBlendFactor =
        MTLBlendFactorOneMinusSourceAlpha;
    desc.colorAttachments[0].writeMask =
        effective.color_write_mask
            ? (MTLColorWriteMask)effective.color_write_mask
            : MTLColorWriteMaskAll;

    NSError *error = nil;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:desc error:&error];
    if (pipeline == nil) {
        pthread_mutex_unlock(&g_lock);
        return NULL;
    }
    record.pipelines[cacheKey] = pipeline;
    void *out = (__bridge void *)pipeline;
    pthread_mutex_unlock(&g_lock);
    return out;
}

void *eu4_gfx_shader_pipeline(const void *shader, void *mtl_device) {
    id<MTLDevice> device =
        mtl_device != NULL ? (__bridge id<MTLDevice>)mtl_device : metal_device();
    EU4GfxPipelineKey key = {};
    key.mtl_device = (__bridge void *)device;
    key.pixel_format = (unsigned)MTLPixelFormatBGRA8Unorm;
    key.sample_count = 1;
    key.color_write_mask = (unsigned)MTLColorWriteMaskAll;
    key.blend_enabled = 1;
    return eu4_gfx_shader_pipeline_for_key(shader, mtl_device, &key);
}
