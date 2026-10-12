#include "../benchmark/eu4_r0_control.h"

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        return 2;
    }
    FILE *file = fopen(argv[1], "rb");
    if (!file) {
        return 3;
    }
    eu4_r0_control_page_t page;
    if (fread((void *)&page, 1, sizeof(page), file) != sizeof(page)) {
        fclose(file);
        return 4;
    }
    fclose(file);
    eu4_r0_control_snapshot_t snap = {0};
    if (!eu4_r0_control_consume(&page, &snap) || !snap.valid) {
        return 5;
    }
    printf("%u %u %u\n", (unsigned)snap.generation, (unsigned)snap.mode, (unsigned)snap.scenario_id);
    return 0;
}
