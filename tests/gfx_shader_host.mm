#include "eu4_gfx_shader.h"

#include <new>
#include <stdint.h>

int main(void) {
    auto *object = static_cast<uint8_t *>(eu4_gfx_create_shader());
    if (object == nullptr) {
        return 1;
    }
    if (*reinterpret_cast<uint32_t *>(object) != 0) {
        return 2;
    }
    if (*reinterpret_cast<uint64_t *>(object + 4) != 0) {
        return 3;
    }
    ::operator delete(object);
    return 0;
}
