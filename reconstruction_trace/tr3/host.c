/* Controlled x86-64 host. It calls tr3_subject twice and prints the results.
 * A startup dylib may intercept the first call. This is not EU4. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern uint64_t tr3_subject(uint64_t *cell);

int main(void) {
    uint64_t *cell = calloc(1, sizeof(*cell));
    if (cell == NULL) return 1;
    *cell = 0x100000000ULL;
    uint64_t first = tr3_subject(cell);
    uint64_t second = tr3_subject(cell);
    const uint8_t *entry = (const uint8_t *)tr3_subject;
    printf("{\"calls\":2,\"first\":\"0x%llx\",\"second\":\"0x%llx\",\"cell\":\"0x%llx\","
           "\"entry\":\"",
           (unsigned long long)first, (unsigned long long)second, (unsigned long long)*cell);
    for (int i = 0; i < 5; i++) printf("%02x", entry[i]);
    printf("\"}\n");
    if (first != 0x100000001ULL || second != 0x100000002ULL || *cell != 0x100000002ULL) return 1;
    return 0;
}
