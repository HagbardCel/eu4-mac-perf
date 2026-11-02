#include "eu4_gfx_shader.h"

#include <new>
#include <stdint.h>
#include <string.h>

void *eu4_gfx_create_shader(void) {
    auto *object = static_cast<uint8_t *>(::operator new(0x78));
    *reinterpret_cast<uint32_t *>(object) = 0;
    uint64_t zero = 0;
    memcpy(object + 4, &zero, sizeof zero);
    return object;
}

int eu4_gfx_add_vertex_shader(void *unused, void *shader, const char *source) {
    (void)unused;
    (void)shader;
    (void)source;
    return 0;
}
