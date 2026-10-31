#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    const char *path = getenv("EU4_METAL_COEXIST_LIB");
    if (path == NULL) {
        fprintf(stderr, "EU4_METAL_COEXIST_LIB is unset\n");
        return 2;
    }
    void *library = dlopen(path, RTLD_NOW);
    if (library == NULL) {
        fprintf(stderr, "%s\n", dlerror());
        return 1;
    }
    return 0;
}
