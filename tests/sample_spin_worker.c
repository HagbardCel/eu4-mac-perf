#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    printf("MAIN_TID=%llu\n", (unsigned long long)tid);
    fflush(stdout);
    volatile uint64_t counter = 0;
    for (int second = 0; second < 5; second++) {
        uint64_t limit = counter + 50000000ull;
        while (counter < limit) {
            counter++;
        }
        usleep(1000);
    }
    return 0;
}
