#include "eu4_gfx_shader.h"

#include <new>
#include <stdint.h>

void *eu4_gfx_create_shader(void) {
    auto *object = static_cast<uint8_t *>(::operator new(0x78));
    *reinterpret_cast<uint32_t *>(object) = 0;
    *reinterpret_cast<uint64_t *>(object + 4) = 0;
    return object;
}
