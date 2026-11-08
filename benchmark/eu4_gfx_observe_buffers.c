#include "eu4_gfx_observe_buffers.h"

#include <stdio.h>
#include <string.h>

/* Capture-local identity for a vertex buffer. A pointer is never compared to a
   Metal address; it only maps an observed header to a stable vb_<n> label. The
   passive gateway cannot observe _GfxDestroyVertexBuffer (its entry prefix has
   a relative branch), so identity is guaranteed only across observed creates.
   A later create returning a previously seen pointer retires the old label. */
#define OBSERVE_RESOURCE_MAX 4096u

typedef struct {
    uintptr_t header;
    char id[EU4_GFX_OBSERVE_RESOURCE_ID_SIZE];
    int known;
} observed_resource;

static observed_resource g_resources[OBSERVE_RESOURCE_MAX];
static unsigned g_resource_count;
static unsigned g_next_resource_id = 1;

/* Last slot-0 constant buffer set. The draw cross-check compares the draw-time
   array against these bytes and the same header pointer. */
static uint8_t g_last_constant[64];
static size_t g_last_constant_size;
static uint64_t g_last_constant_call_id;
static uintptr_t g_last_constant_header;
static unsigned g_next_constant_id = 1;

static void retire_header(uintptr_t header) {
    for (unsigned i = 0; i < g_resource_count; i++) {
        if (g_resources[i].header == header) {
            g_resources[i] = g_resources[g_resource_count - 1];
            g_resource_count -= 1;
            return;
        }
    }
}

static observed_resource *find_header(uintptr_t header) {
    if (header == 0) {
        return NULL;
    }
    for (unsigned i = 0; i < g_resource_count; i++) {
        if (g_resources[i].header == header) {
            return &g_resources[i];
        }
    }
    return NULL;
}

static const char *lookup_header(uintptr_t header) {
    observed_resource *found = find_header(header);
    return found != NULL ? found->id : NULL;
}

static const char *assign_header(uintptr_t header, int known) {
    if (header == 0) {
        return NULL;
    }
    if (find_header(header) != NULL) {
        retire_header(header);
    }
    if (g_resource_count >= OBSERVE_RESOURCE_MAX) {
        return NULL;
    }
    observed_resource *slot = &g_resources[g_resource_count++];
    slot->header = header;
    slot->known = known ? 1 : 0;
    (void)snprintf(slot->id, sizeof(slot->id), "vb_%u", g_next_resource_id++);
    return slot->id;
}

void eu4_gfx_observe_buffers_reset(void) {
    memset(g_resources, 0, sizeof(g_resources));
    g_resource_count = 0;
    g_next_resource_id = 1;
    memset(g_last_constant, 0, sizeof(g_last_constant));
    g_last_constant_size = 0;
    g_last_constant_call_id = 0;
    g_last_constant_header = 0;
    g_next_constant_id = 1;
}

static int read_i32(const void *src, int32_t *out) {
    return eu4_gfx_observe_read(src, out, sizeof(*out)) ? 0 : -1;
}

void eu4_gfx_observe_vb_create_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    int32_t count = (int32_t)frame->rdx;
    int32_t stride = (int32_t)frame->rcx;
    const void *data = (const void *)(uintptr_t)frame->rsi;
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_VERTEX_CREATE);
    call->retain = 1;
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(meta, sizeof(meta),
                   ",\"count\":%d,\"stride\":%d,\"usage\":%d", count, stride,
                   (int32_t)frame->r8);
    eu4_gfx_observe_set_meta(call, meta);
    if (count < 0 || stride < 0) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    size_t size = (size_t)count * (size_t)stride;
    if (size > 0) {
        if (data == NULL) {
            /* A null source is an unknown-capacity buffer, not a read failure. */
            call->payload_ok = 1;
            call->payload_size = 0;
            eu4_gfx_observe_set_resource(call, "", 0);
            return;
        }
        if (!eu4_gfx_observe_try_payload(call, data, size)) {
            eu4_gfx_observe_set_resource(call, "", 0);
            return;
        }
        eu4_gfx_observe_set_resource(call, "", 1);
    } else {
        eu4_gfx_observe_set_resource(call, "", data != NULL || count == 0);
    }
}

void eu4_gfx_observe_vb_create_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record) {
    (void)record;
    uintptr_t header = (uintptr_t)frame->rax;
    const char *id = assign_header(header, call->contents_known);
    if (id == NULL) {
        eu4_gfx_observe_set_resource(call, "", 0);
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    eu4_gfx_observe_set_resource(call, id, call->contents_known);
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(meta, sizeof(meta),
                   "%s,\"header_non_null\":true,\"header\":%llu", call->meta,
                   (unsigned long long)header);
    memcpy(call->meta, meta, sizeof(call->meta));
}

void eu4_gfx_observe_vb_update_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    uintptr_t header = (uintptr_t)frame->rsi;
    observed_resource *resource = find_header(header);
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE);
    call->retain = 1;
    if (resource == NULL) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    int32_t stride = 0;
    int32_t stored_count = 0;
    if (read_i32((const void *)header, &stride) != 0 ||
        read_i32((const void *)(header + 4), &stored_count) != 0) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    int32_t count = (int32_t)frame->rcx;
    if (count <= 0) {
        count = stored_count;
    }
    int32_t offset = (int32_t)frame->r8;
    int32_t partial = (int32_t)frame->r9;
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(meta, sizeof(meta),
                   ",\"count\":%d,\"stride\":%d,\"offset\":%d,\"partial\":%d",
                   count, stride, offset, partial);
    eu4_gfx_observe_set_meta(call, meta);
    eu4_gfx_observe_set_resource(call, resource->id, resource->known);
    if (count < 0 || stride < 0) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    size_t size = (size_t)count * (size_t)stride;
    if (size > 0) {
        const void *data = (const void *)(uintptr_t)frame->rdx;
        if (data == NULL) {
            call->payload_ok = 1;
            call->payload_size = 0;
            resource->known = 0;
            eu4_gfx_observe_set_resource(call, resource->id, 0);
            return;
        }
        if (!eu4_gfx_observe_try_payload(call, data, size)) {
            resource->known = 0;
            eu4_gfx_observe_set_resource(call, resource->id, 0);
            return;
        }
        if (partial == 0) {
            resource->known = 1;
        }
        eu4_gfx_observe_set_resource(call, resource->id, resource->known);
    }
}

void eu4_gfx_observe_vb_bind_entry(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call) {
    int32_t count = (int32_t)frame->rsi;
    const void *headers = (const void *)(uintptr_t)frame->rdx;
    int32_t start = (int32_t)frame->rcx;
    const void *offsets = (const void *)(uintptr_t)frame->r8;
    const void *words = (const void *)(uintptr_t)frame->r9;
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_VERTEX_BIND);
    call->retain = 1;
    if (count < 0 || count > 4 || (count > 0 && headers == NULL)) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    int used = snprintf(meta, sizeof(meta),
                        ",\"count\":%d,\"start\":%d,\"offsets_null\":%s,"
                        "\"words_null\":%s,\"ids\":[",
                        count, start, offsets == NULL ? "true" : "false",
                        words == NULL ? "true" : "false");
    if (used < 0 || (size_t)used >= sizeof(meta)) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    for (int32_t i = 0; i < count; i++) {
        uintptr_t header = 0;
        if (!eu4_gfx_observe_read((const uint8_t *)headers + (size_t)i * 8,
                                  &header, sizeof(header))) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        const char *id = lookup_header(header);
        if (id == NULL) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        int wrote = snprintf(meta + used, sizeof(meta) - (size_t)used,
                             "%s\"%s\"", i == 0 ? "" : ",", id);
        if (wrote < 0 || (size_t)(used + wrote) >= sizeof(meta)) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        used += wrote;
    }
    int wrote = snprintf(meta + used, sizeof(meta) - (size_t)used,
                         "],\"offsets\":[");
    if (wrote < 0 || (size_t)(used + wrote) >= sizeof(meta)) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    used += wrote;
    for (int32_t i = 0; i < count; i++) {
        int32_t value = 0;
        if (offsets != NULL &&
            !eu4_gfx_observe_read((const uint8_t *)offsets + (size_t)i * 4,
                                  &value, sizeof(value))) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        wrote = snprintf(meta + used, sizeof(meta) - (size_t)used,
                         "%s%d", i == 0 ? "" : ",", value);
        if (wrote < 0 || (size_t)(used + wrote) >= sizeof(meta)) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        used += wrote;
    }
    wrote = snprintf(meta + used, sizeof(meta) - (size_t)used, "],\"words\":[");
    if (wrote < 0 || (size_t)(used + wrote) >= sizeof(meta)) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    used += wrote;
    for (int32_t i = 0; i < count; i++) {
        int32_t value = 0;
        if (words != NULL &&
            !eu4_gfx_observe_read((const uint8_t *)words + (size_t)i * 4,
                                  &value, sizeof(value))) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        wrote = snprintf(meta + used, sizeof(meta) - (size_t)used,
                         "%s%d", i == 0 ? "" : ",", value);
        if (wrote < 0 || (size_t)(used + wrote) >= sizeof(meta)) {
            eu4_gfx_observe_fail_fields(call);
            return;
        }
        used += wrote;
    }
    (void)snprintf(meta + used, sizeof(meta) - (size_t)used, "]");
    eu4_gfx_observe_set_meta(call, meta);
}

void eu4_gfx_observe_cb_entry(eu4_gfx_capture_frame *frame,
                              eu4_gfx_observe_call *call) {
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_CONSTANT_SET);
    const int32_t slot = (int32_t)frame->rcx;
    const int32_t selector = (int32_t)frame->r9;
    const uintptr_t header = (uintptr_t)frame->rsi;
    call->retain = slot == 0 ? 1 : 0;

    int32_t length = selector;
    if (selector == -1) {
        int32_t first = 0;
        length = read_i32((const void *)header, &first) == 0 ? first : -1;
    }
    int32_t vec4 = 0;
    if (length >= 0) {
        /* Same truncating signed divide by four the body uses. */
        int32_t adjusted = length + 3;
        if (adjusted < 0) {
            adjusted = length + 6;
        }
        vec4 = adjusted >> 2;
    }
    uintptr_t data = 0;
    int data_ok = eu4_gfx_observe_read((const void *)(header + 8), &data,
                                       sizeof(data)) &&
                  data != 0;
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    (void)snprintf(meta, sizeof(meta),
                   ",\"slot\":%d,\"selector\":%d,\"header\":%llu,"
                   "\"data_ptr\":%llu,\"data_size\":%d,\"vec4_count\":%d",
                   slot, selector, (unsigned long long)header,
                   (unsigned long long)data,
                   vec4 > 0 && vec4 <= 64 ? vec4 * 16 : 0, vec4);
    eu4_gfx_observe_set_meta(call, meta);

    char id[EU4_GFX_OBSERVE_RESOURCE_ID_SIZE];
    (void)snprintf(id, sizeof(id), "cb_%u", g_next_constant_id++);
    if (slot == 0 && vec4 >= 4 && vec4 <= 64 && data_ok) {
        if (eu4_gfx_observe_read_bytes((const void *)data, g_last_constant,
                                       sizeof(g_last_constant))) {
            g_last_constant_size = sizeof(g_last_constant);
            g_last_constant_call_id = call->call_id;
            g_last_constant_header = header;
        }
        if (eu4_gfx_observe_try_payload(call, (const void *)data,
                                        (size_t)vec4 * 16u)) {
            eu4_gfx_observe_set_resource(call, id, 1);
        } else {
            eu4_gfx_observe_set_resource(call, id, 0);
        }
    } else {
        eu4_gfx_observe_set_resource(call, id, 0);
    }
}

int eu4_gfx_observe_constant_matches(uintptr_t header, const uint8_t *bytes,
                                     size_t n, uint64_t *call_id) {
    if (header == 0 || bytes == NULL || n == 0 ||
        g_last_constant_size < n || g_last_constant_header != header ||
        memcmp(g_last_constant, bytes, n) != 0) {
        return 0;
    }
    if (call_id != NULL) {
        *call_id = g_last_constant_call_id;
    }
    return 1;
}
