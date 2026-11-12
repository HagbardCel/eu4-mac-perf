/* Stage-aware exact-hash admission. Builds no engine scene; it checks that a
   qualified vertex hash cannot admit a pixel source and vice versa. */
#include "eu4_gfx_shader.h"

#include <Metal/Metal.h>
#include <stdio.h>
#include <string.h>

extern "C" void *eu4_gfx_backend_token(void) { return nullptr; }

static int failures;

static void check(int condition, const char *what) {
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", what);
        failures++;
    }
}

static void *make_object(void) {
    void *object = eu4_gfx_create_shader();
    if (object == nullptr) {
        fprintf(stderr, "FAIL: shader create\n");
        failures++;
    }
    return object;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <qualified-manifest>\n", argv[0]);
        return 2;
    }
    eu4_gfx_shader_test_reset();
    eu4_gfx_shader_set_allow_transitional_markers(0);

    void *object = make_object();
    if (object == nullptr) {
        return 3;
    }
    check(eu4_gfx_add_vertex_shader(nullptr, object, "stage-vs") == 0,
          "unknown source accepted before manifest");
    eu4_gfx_destroy_shader(object);

    int loaded = eu4_gfx_shader_load_qualified_sources(argv[1]);
    check(loaded == 2, "valid manifest records");
    check(eu4_gfx_shader_qualified_count() == 2, "qualified count");

    object = make_object();
    check(eu4_gfx_add_vertex_shader(nullptr, object, "stage-vs") == 1,
          "qualified Simple VS accepted as vertex");
    check(eu4_gfx_add_pixel_shader(nullptr, object, "stage-ps") == 1,
          "qualified Simple PS accepted as pixel");
    check(eu4_gfx_link_shader(nullptr, object) == 1, "linked after both stages");
    eu4_gfx_destroy_shader(object);

    object = make_object();
    check(eu4_gfx_add_vertex_shader(nullptr, object, "stage-ps") == 0,
          "qualified Simple PS rejected as vertex");
    check(eu4_gfx_add_pixel_shader(nullptr, object, "stage-vs") == 0,
          "qualified Simple VS rejected as pixel");
    eu4_gfx_destroy_shader(object);

    object = make_object();
    check(eu4_gfx_add_vertex_shader(nullptr, object, "unknown-vs") == 0,
          "unknown vertex hash rejected");
    check(eu4_gfx_add_pixel_shader(nullptr, object, "unknown-ps") == 0,
          "unknown pixel hash rejected");
    eu4_gfx_destroy_shader(object);

    if (argc >= 3) {
        /* Malformed later record must not partially admit the valid prefix. */
        check(eu4_gfx_shader_load_qualified_sources(argv[2]) == -1,
              "malformed manifest rejected");
        check(eu4_gfx_shader_qualified_count() == 2,
              "failed manifest left the active set unchanged");
        object = make_object();
        check(eu4_gfx_add_vertex_shader(nullptr, object, "stage-vs") == 1,
              "previous qualification survives failed manifest");
        eu4_gfx_destroy_shader(object);
    }
    if (argc >= 4) {
        check(eu4_gfx_shader_load_qualified_sources(argv[3]) == -1,
              "invalid stage/effect manifest rejected");
        check(eu4_gfx_shader_qualified_count() == 2,
              "invalid stage/effect left the active set unchanged");
    }

    if (failures != 0) {
        return 1;
    }
    printf("stage_ok\n");
    return 0;
}
