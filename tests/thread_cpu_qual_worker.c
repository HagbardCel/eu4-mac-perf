#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile uint64_t g_counter;

static void *spin_loop(void *arg) {
    (void)arg;
    struct timespec start = {0};
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        g_counter++;
        struct timespec now = {0};
        clock_gettime(CLOCK_MONOTONIC, &now);
        double elapsed = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) / 1e9;
        if (elapsed >= 8.0) {
            break;
        }
    }
    return NULL;
}

static void print_main_tid(void) {
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    printf("MAIN_TID=%llu\n", (unsigned long long)tid);
    fflush(stdout);
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "spin";
    print_main_tid();
    if (strcmp(mode, "sleep") == 0) {
        sleep(8);
        return 0;
    }
    if (strcmp(mode, "dual") == 0) {
        pthread_t worker;
        pthread_create(&worker, NULL, spin_loop, NULL);
        spin_loop(NULL);
        pthread_join(worker, NULL);
        return 0;
    }
    if (strcmp(mode, "churn") == 0) {
        for (int round = 0; round < 20; round++) {
            pthread_t worker;
            pthread_create(&worker, NULL, spin_loop, NULL);
            struct timespec start = {0};
            clock_gettime(CLOCK_MONOTONIC, &start);
            for (;;) {
                g_counter++;
                struct timespec now = {0};
                clock_gettime(CLOCK_MONOTONIC, &now);
                double elapsed = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) / 1e9;
                if (elapsed >= 0.05) {
                    break;
                }
            }
            pthread_join(worker, NULL);
            usleep(10000);
        }
        sleep(1);
        return 0;
    }
    spin_loop(NULL);
    return 0;
}
