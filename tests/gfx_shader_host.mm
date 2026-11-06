#include "eu4_gfx_shader.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Offline host has no gfx backend. */
extern "C" void *eu4_gfx_backend_token(void) { return nullptr; }

int main(void) {
    eu4_gfx_shader_test_reset();

    void *object = eu4_gfx_create_shader();
    if (object == nullptr) {
        return 1;
    }
    uint32_t name = *reinterpret_cast<uint32_t *>(object);
    uint64_t qword = 1;
    memcpy(&qword, static_cast<uint8_t *>(object) + 4, sizeof qword);
    if (name != 0 || qword != 0) {
        return 2;
    }
    uint64_t life1 = eu4_gfx_shader_lifetime_id(object);
    if (life1 == 0) {
        return 3;
    }

    if (eu4_gfx_add_vertex_shader(nullptr, object, "not a supported shader") != 0) {
        return 4;
    }
    if (eu4_gfx_add_pixel_shader(nullptr, object, "not a supported shader") != 0) {
        return 5;
    }
    if (eu4_gfx_link_shader(nullptr, object) != 0) {
        return 6;
    }

    if (eu4_gfx_add_vertex_shader(nullptr, object, eu4_gfx_simple_vertex_source()) != 1) {
        return 7;
    }
    if (eu4_gfx_add_pixel_shader(nullptr, object, eu4_gfx_simple_pixel_source()) != 1) {
        return 8;
    }
    if (eu4_gfx_link_shader(nullptr, object) != 1) {
        return 9;
    }
    if (!eu4_gfx_shader_is_linked(object)) {
        return 10;
    }
    if (eu4_gfx_shader_identity(object) == nullptr) {
        return 11;
    }

    alignas(16) uint8_t master[0x180];
    memset(master, 0, sizeof master);
    struct {
        void *master;
    } ctx = {master};
    eu4_gfx_set_shader(&ctx, object);
    void *current = nullptr;
    memcpy(&current, master + 0x70, sizeof current);
    if (current != object || master[0x128] != 1 || master[0x170] != 1) {
        return 12;
    }

    void *pipeline = eu4_gfx_shader_pipeline(object, nullptr);
    if (pipeline == nullptr) {
        fprintf(stderr, "pipeline construction failed\n");
        return 13;
    }

    uintptr_t addr = reinterpret_cast<uintptr_t>(object);
    eu4_gfx_destroy_shader(object);
    if (eu4_gfx_shader_lifetime_id(reinterpret_cast<void *>(addr)) != 0) {
        return 14;
    }

    void *object2 = eu4_gfx_create_shader();
    if (object2 == nullptr) {
        return 15;
    }
    uint64_t life2 = eu4_gfx_shader_lifetime_id(object2);
    if (life2 == 0) {
        return 16;
    }
    if (life2 == life1) {
        return 17;
    }
    if (eu4_gfx_add_vertex_shader(nullptr, object2, eu4_gfx_simple_vertex_source()) != 1) {
        return 18;
    }
    if (eu4_gfx_add_pixel_shader(nullptr, object2, "bad") != 0) {
        return 19;
    }
    if (eu4_gfx_link_shader(nullptr, object2) != 0) {
        return 20;
    }

    eu4_gfx_destroy_shader(object2);
    fprintf(stdout, "ok\n");
    return 0;
}
