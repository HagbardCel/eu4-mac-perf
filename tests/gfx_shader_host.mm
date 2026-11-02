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
    return 0;
}
