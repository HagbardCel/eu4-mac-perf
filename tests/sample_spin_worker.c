#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

int main(void) {
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    printf("MAIN_TID=%llu\n", (unsigned long long)tid);
    fflush(stdout);
    struct timespec start = {0};
    clock_gettime(CLOCK_MONOTONIC, &start);
    volatile uint64_t counter = 0;
    for (;;) {
        counter++;
        struct timespec now = {0};
        clock_gettime(CLOCK_MONOTONIC, &now);
        double elapsed = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) / 1e9;
        if (elapsed >= 5.0) {
            break;
        }
    }
    return 0;
}
