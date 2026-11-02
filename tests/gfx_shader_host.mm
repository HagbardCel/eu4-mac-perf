#include "eu4_gfx_shader.h"

#include <new>
#include <stdint.h>
#include <string.h>

int main(void) {
    auto *object = static_cast<uint8_t *>(eu4_gfx_create_shader());
    if (object == nullptr) {
        return 1;
    }
    uint32_t name = *reinterpret_cast<uint32_t *>(object);
    uint64_t qword = 1;
    memcpy(&qword, object + 4, sizeof qword);
    if (name != 0) {
        return 2;
    }
    if (qword != 0) {
        return 3;
    }
    ::operator delete(object);
    uint8_t object_bytes[0x78];
    uint8_t saved[0x78];
    memset(object_bytes, 0x5a, sizeof object_bytes);
    memcpy(saved, object_bytes, sizeof saved);
    if (eu4_gfx_add_vertex_shader(nullptr, object_bytes, reinterpret_cast<const char *>(1)) != 0) {
        return 4;
    }
    if (memcmp(object_bytes, saved, sizeof saved) != 0) {
        return 5;
    }
    uint32_t prior = 0x11223344;
    memcpy(object_bytes + 4, &prior, sizeof prior);
    memcpy(saved, object_bytes, sizeof saved);
    if (eu4_gfx_add_vertex_shader(nullptr, object_bytes, reinterpret_cast<const char *>(1)) != 0) {
        return 6;
    }
    if (memcmp(object_bytes, saved, sizeof saved) != 0) {
        return 7;
    }
    return 0;
}
