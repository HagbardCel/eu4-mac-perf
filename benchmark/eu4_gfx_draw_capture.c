#include "eu4_gfx_draw_capture.h"

#include "eu4_gfx_init.h"
#include "eu4_metal_events.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

/* _GfxDrawIndexed reads the context through (%rdi). The other two entries
   have no verified context layout, so their receipts stay raw. */
enum { EU4_GFX_DRAW_INDEXED = 2, EU4_GFX_DRAW_CONTEXT_WANT = 0x171 };

_Static_assert(offsetof(eu4_gfx_draw_raw, rdi) == 8, "rdi slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, xmm) == 56, "xmm slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, retaddr) == 184, "return slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, stack) == 192, "stack slot");
_Static_assert(sizeof(eu4_gfx_draw_raw) == 256, "raw size");

static int hex_put(const void *src, int n, char *out, int cap) {
    static const char *digits = "0123456789abcdef";
    if (n < 0 || cap < n * 2 + 1) {
        return -1;
    }
    const uint8_t *bytes = src;
    for (int i = 0; i < n; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    out[n * 2] = 0;
    return 0;
}

static const char *symbol_for(uint32_t which) {
    if (which == 1) {
        return "_GfxDraw";
    }
    if (which == EU4_GFX_DRAW_INDEXED) {
        return "_GfxDrawIndexed";
    }
    if (which == 3) {
        return "_GfxDrawIndexedInstanced";
    }
    return NULL;
}

int eu4_gfx_draw_receipt(const eu4_gfx_draw_raw *raw, char *out, int cap) {
    if (raw == NULL || out == NULL || cap < 64) {
        return -1;
    }
    const char *symbol = symbol_for(raw->which);
    if (symbol == NULL) {
        return -1;
    }
    char image[512];
    uint64_t rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe((const void *)(uintptr_t)raw->retaddr, image, sizeof image, &rva, &inside,
                              &thread, &pid);
    int context_want = raw->which == EU4_GFX_DRAW_INDEXED ? EU4_GFX_DRAW_CONTEXT_WANT : 0;
    int context_got = 0;
    uint8_t context[EU4_GFX_DRAW_CONTEXT_WANT];
    memset(context, 0, sizeof context);
    if (context_want > 0) {
        uint8_t slot[8];
        int slot_got = 0;
        if (eu4_metal_events_read((const void *)(uintptr_t)raw->rdi, slot, 8, &slot_got) == 0 &&
            slot_got == 8) {
            uint64_t context_ptr = 0;
            memcpy(&context_ptr, slot, sizeof context_ptr);
            if (context_ptr != 0) {
                (void)eu4_metal_events_read((const void *)(uintptr_t)context_ptr, context, context_want,
                                            &context_got);
            }
        }
    }
    int complete = context_got == context_want;
    char stack_hex[129];
    char xmm_hex[8][33];
    char context_hex[EU4_GFX_DRAW_CONTEXT_WANT * 2 + 1];
    context_hex[0] = 0;
    if (hex_put(raw->stack, 64, stack_hex, (int)sizeof stack_hex) != 0) {
        return -1;
    }
    for (int i = 0; i < 8; i++) {
        if (hex_put(raw->xmm[i], 16, xmm_hex[i], (int)sizeof xmm_hex[i]) != 0) {
            return -1;
        }
    }
    if (context_got > 0 && hex_put(context, context_got, context_hex, (int)sizeof context_hex) != 0) {
        return -1;
    }
    char chain_buf[80];
    char caller_buf[80];
    const char *chain = "";
    const char *caller = "null";
    if (inside) {
        snprintf(chain_buf, sizeof chain_buf, "\"0x%llx\"", (unsigned long long)rva);
        snprintf(caller_buf, sizeof caller_buf, "\"0x%llx\"", (unsigned long long)rva);
        chain = chain_buf;
        caller = caller_buf;
    }
    int length = snprintf(
        out, (size_t)cap,
        "{\"symbol\":\"%s\",\"invocation_id\":1,\"provenance\":\"%s\",\"call_chain\":[%s],"
        "\"image\":\"%s\",\"caller_rva\":%s,\"pid\":%d,\"thread\":%llu,"
        "\"rdi\":\"0x%llx\",\"rsi\":\"0x%llx\",\"rdx\":\"0x%llx\",\"rcx\":\"0x%llx\","
        "\"r8\":\"0x%llx\",\"r9\":\"0x%llx\",\"stack\":\"%s\","
        "\"xmm\":[\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\"],"
        "\"context_want\":%d,\"context_got\":%d,\"context_complete\":%s,\"context\":\"%s\","
        "\"unresolved_dependencies\":0}\n",
        symbol, inside ? "game" : "rejected", chain, image, caller, pid, (unsigned long long)thread,
        (unsigned long long)raw->rdi, (unsigned long long)raw->rsi, (unsigned long long)raw->rdx,
        (unsigned long long)raw->rcx, (unsigned long long)raw->r8, (unsigned long long)raw->r9,
        stack_hex, xmm_hex[0], xmm_hex[1], xmm_hex[2], xmm_hex[3], xmm_hex[4], xmm_hex[5],
        xmm_hex[6], xmm_hex[7], context_want, context_got, complete ? "true" : "false",
        context_hex);
    if (length < 0 || length >= cap) {
        return -1;
    }
    return 0;
}

void eu4_gfx_draw_capture(const eu4_gfx_draw_raw *raw) {
    char body[8192];
    const char *reason = "error";
    const char *symbol = "_GfxDraw";
    if (raw != NULL) {
        const char *named = symbol_for(raw->which);
        if (named != NULL) {
            symbol = named;
        }
        if (eu4_gfx_draw_receipt(raw, body, (int)sizeof body) == 0 &&
            eu4_metal_events_write("draw_invocation.json", body) == 0 &&
            strstr(body, "\"provenance\":\"game\"") != NULL) {
            reason = "draw";
        }
    }
    const void *caller = raw == NULL ? NULL : (const void *)(uintptr_t)raw->retaddr;
    eu4_gfx_stop(symbol, reason, caller);
}
