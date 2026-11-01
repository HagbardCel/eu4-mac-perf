#import "../benchmark/eu4_b2_draw.h"

#import <stdio.h>
#import <stdlib.h>
#import <string.h>

static uint8_t *read_file(const char *path, int *out_size) {
    FILE *in = fopen(path, "rb");
    if (in == NULL) return NULL;
    if (fseek(in, 0, SEEK_END) != 0) {
        fclose(in);
        return NULL;
    }
    long size = ftell(in);
    if (size <= 0 || size > (64 << 20)) {
        fclose(in);
        return NULL;
    }
    rewind(in);
    uint8_t *bytes = (uint8_t *)malloc((size_t)size);
    if (bytes == NULL || fread(bytes, 1, (size_t)size, in) != (size_t)size) {
        free(bytes);
        fclose(in);
        return NULL;
    }
    fclose(in);
    *out_size = (int)size;
    return bytes;
}

static int json_int(const char *json, const char *key, int *out) {
    char pattern[64];
    snprintf(pattern, sizeof pattern, "\"%s\"", key);
    const char *found = strstr(json, pattern);
    if (found == NULL) return -1;
    found = strchr(found, ':');
    if (found == NULL) return -1;
    *out = atoi(found + 1);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: b2_draw_host fixture-dir\n");
        return 1;
    }
    char path[1100];
    snprintf(path, sizeof path, "%s/fixture.json", argv[1]);
    int json_size = 0;
    char *json = (char *)read_file(path, &json_size);
    int width = 0, height = 0, cap = 0;
    if (json == NULL || json_int(json, "width", &width) || json_int(json, "height", &height) ||
        json_int(json, "max_channel_abs", &cap)) {
        fprintf(stderr, "fixture json\n");
        return 1;
    }
    int pixels = width * height * 4;
    snprintf(path, sizeof path, "%s/reference.rgba", argv[1]);
    int ref_size = 0, clear_size = 0;
    uint8_t *reference = read_file(path, &ref_size);
    snprintf(path, sizeof path, "%s/clear.rgba", argv[1]);
    uint8_t *cleared = read_file(path, &clear_size);
    if (reference == NULL || cleared == NULL || ref_size != pixels || clear_size != pixels) {
        fprintf(stderr, "reference pixels\n");
        return 1;
    }
    if (memcmp(reference, cleared, (size_t)pixels) == 0) {
        fprintf(stderr, "reference matches the clear baseline\n");
        return 1;
    }
    uint8_t *metal = (uint8_t *)malloc((size_t)pixels);
    if (metal == NULL || eu4_b2_draw_fixture(argv[1], metal, pixels) != 0) {
        fprintf(stderr, "metal draw failed\n");
        return 1;
    }
    int worst = 0;
    for (int i = 0; i < pixels; i++) {
        int delta = (int)metal[i] - (int)reference[i];
        if (delta < 0) delta = -delta;
        if (delta > worst) worst = delta;
    }
    printf("%d\n", worst);
    free(json);
    free(reference);
    free(cleared);
    free(metal);
    return worst <= cap ? 0 : 1;
}
