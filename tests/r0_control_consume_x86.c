#include "../benchmark/eu4_r0_control.h"

#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int touch_file(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        return 1;
    }
    close(fd);
    return 0;
}

static bool validate_snapshot(const eu4_r0_control_snapshot_t *snap) {
    if (!snap || !snap->valid) {
        return false;
    }
    if (snap->version != EU4_R0_CONTROL_VERSION) {
        return false;
    }
    if (snap->mode != (snap->generation % 3u)) {
        return false;
    }
    if (snap->scenario_id != (snap->generation % 11u)) {
        return false;
    }
    return true;
}

static int run_stress(const char *control_path, const char *ready_path, const char *done_path,
                      const char *result_path, int min_distinct) {
    int fd = open(control_path, O_RDONLY);
    if (fd < 0) {
        return 10;
    }
    void *mapping = mmap(NULL, EU4_R0_CONTROL_PAGE_SIZE, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (mapping == MAP_FAILED) {
        return 11;
    }
    if (touch_file(ready_path) != 0) {
        munmap(mapping, EU4_R0_CONTROL_PAGE_SIZE);
        return 12;
    }
    const volatile eu4_r0_control_page_t *page = (const volatile eu4_r0_control_page_t *)mapping;
    unsigned long reads = 0;
    unsigned long violations = 0;
    unsigned char seen[512];
    memset(seen, 0, sizeof(seen));
    int distinct = 0;
    while (access(done_path, F_OK) != 0) {
        eu4_r0_control_snapshot_t snap = {0};
        if (eu4_r0_control_consume(page, &snap) && snap.valid) {
            reads++;
            if (!validate_snapshot(&snap)) {
                violations++;
            } else if (snap.generation < (uint32_t)sizeof(seen) && !seen[snap.generation]) {
                seen[snap.generation] = 1;
                distinct++;
            }
        }
    }
    munmap(mapping, EU4_R0_CONTROL_PAGE_SIZE);
    FILE *result = fopen(result_path, "w");
    if (!result) {
        return 13;
    }
    fprintf(result, "reads=%lu violations=%lu distinct=%d\n", reads, violations, distinct);
    fclose(result);
    if (violations > 0 || reads < 1000 || distinct < min_distinct) {
        return 14;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        return 2;
    }
    if (argc >= 7 && strcmp(argv[2], "stress") == 0) {
        return run_stress(argv[1], argv[3], argv[4], argv[5], atoi(argv[6]));
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
