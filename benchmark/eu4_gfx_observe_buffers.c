#include "eu4_gfx_observe_buffers.h"

#include <stdio.h>
#include <string.h>

/* Capture-local identity for a vertex buffer. A pointer is never compared to a
   Metal address; it only maps an observed header to a stable vb_<n> label. The
   passive gateway cannot observe _GfxDestroyVertexBuffer (its entry prefix has
   a relative branch), so identity is guaranteed only across observed creates.
   A later create returning a previously seen pointer retires the old label. */
#define OBSERVE_RESOURCE_MAX 256u

typedef struct {
    uintptr_t header;
    char id[EU4_GFX_OBSERVE_RESOURCE_ID_SIZE];
    int known;
} observed_resource;

static observed_resource g_resources[OBSERVE_RESOURCE_MAX];
static unsigned g_resource_count;
static unsigned g_next_resource_id = 1;

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
        eu4_gfx_observe_set_payload(call, data, size);
        if (!call->payload_ok) {
            eu4_gfx_observe_set_resource(call, "", 0);
            eu4_gfx_observe_fail_fields(call);
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
    (void)snprintf(meta, sizeof(meta), "%s,\"header_non_null\":true",
                   call->meta);
    memcpy(call->meta, meta, sizeof(call->meta));
}

void eu4_gfx_observe_vb_update_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    uintptr_t header = (uintptr_t)frame->rsi;
    observed_resource *resource = find_header(header);
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE);
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
        eu4_gfx_observe_set_payload(call, (const void *)(uintptr_t)frame->rdx,
                                    size);
        if (!call->payload_ok) {
            eu4_gfx_observe_fail_fields(call);
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
    if (count < 0 || count > 4 || (count > 0 && headers == NULL)) {
        eu4_gfx_observe_fail_fields(call);
        return;
    }
    char meta[EU4_GFX_OBSERVE_META_SIZE];
    int used = snprintf(meta, sizeof(meta), ",\"count\":%d,\"start\":%d,\"ids\":[",
                        count, start);
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
