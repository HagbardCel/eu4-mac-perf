/* Controlled x86-64 host. One fixed sequence: the 10-byte subject twice,
 * the outside snapshot twice, then a call and an out-of-range jump.
 * A startup dylib may intercept the first call of whichever entry the
 * descriptor named. This is not EU4. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define TR3_SNAP_BYTES 656

extern uint64_t tr3_subject(uint64_t *cell);
extern void tr3_snap(void);
extern void tr3_calls(void);
extern void tr3_jump_out(void);
extern uint8_t tr3_snap_entry0[], tr3_snap_exit0[], tr3_snap_entry1[], tr3_snap_exit1[];
extern uint64_t tr3_snap_n;

static void print_snap(const char *name, const uint8_t *bytes) {
    printf(",\"%s\":\"", name);
    for (size_t i = 0; i < TR3_SNAP_BYTES; i++) printf("%02x", bytes[i]);
    putchar('"');
}

int main(void) {
    uint64_t *cell = calloc(1, sizeof(*cell));
    if (cell == NULL) return 1;
    *cell = 0x100000000ULL;
    uint64_t first = tr3_subject(cell);
    uint64_t second = tr3_subject(cell);
    tr3_snap();
    tr3_snap();
    tr3_calls();
    tr3_jump_out();
    const uint8_t *entry = (const uint8_t *)tr3_subject;
    printf("{\"calls\":2,\"first\":\"0x%llx\",\"second\":\"0x%llx\",\"cell\":\"0x%llx\","
           "\"entry\":\"",
           (unsigned long long)first, (unsigned long long)second, (unsigned long long)*cell);
    for (int i = 0; i < 5; i++) printf("%02x", entry[i]);
    printf("\",\"snaps\":%llu", (unsigned long long)tr3_snap_n);
    print_snap("snap_entry0", tr3_snap_entry0);
    print_snap("snap_exit0", tr3_snap_exit0);
    print_snap("snap_entry1", tr3_snap_entry1);
    print_snap("snap_exit1", tr3_snap_exit1);
    printf("}\n");
    if (first != 0x100000001ULL || second != 0x100000002ULL || *cell != 0x100000002ULL) return 1;
    if (tr3_snap_n != 2) return 1;
    return 0;
}
