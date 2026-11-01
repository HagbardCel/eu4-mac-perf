#include "eu4_metal_events.h"

#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static char event_dir[1024];
static uintptr_t image_base;
static uintptr_t image_end;

void eu4_metal_events_set_dir(const char *dir) {
    event_dir[0] = 0;
    if (dir == NULL || dir[0] != '/') {
        return;
    }
    snprintf(event_dir, sizeof event_dir, "%s", dir);
}

void eu4_metal_events_set_image(uintptr_t base, uintptr_t end) {
    image_base = base;
    image_end = end;
}

int eu4_metal_events_write(const char *name, const char *body) {
    if (event_dir[0] == 0 || name == NULL || body == NULL) {
        return -1;
    }
    char final[1280];
    char temp[1280];
    int final_len = snprintf(final, sizeof final, "%s/%s", event_dir, name);
    int temp_len = snprintf(temp, sizeof temp, "%s/%s.partial", event_dir, name);
    if (final_len < 0 || temp_len < 0 || (size_t)final_len >= sizeof final ||
        (size_t)temp_len >= sizeof temp) {
        return -1;
    }
    if (access(final, F_OK) == 0) {
        return 0;
    }
    FILE *out = fopen(temp, "w");
    if (out == NULL) {
        return -1;
    }
    int failed = fputs(body, out) == EOF || fflush(out) != 0;
    int fd = fileno(out);
    if (!failed) {
        failed = fd < 0 || fsync(fd) != 0;
    }
    if (fclose(out) != 0) {
        failed = 1;
    }
    if (failed || rename(temp, final) != 0) {
        (void)unlink(temp);
        return -1;
    }
    return 0;
}

static void json_escape(const char *text, char *out, size_t cap) {
    size_t n = 0;
    if (text == NULL) {
        text = "";
    }
    for (size_t i = 0; text[i] != 0 && n + 2 < cap; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == '"' || c == '\\') {
            if (n + 2 >= cap) {
                break;
            }
            out[n++] = '\\';
            out[n++] = (char)c;
        } else if (c < 0x20 || c > 0x7e) {
            break;
        } else {
            out[n++] = (char)c;
        }
    }
    out[n] = 0;
}

void eu4_metal_events_stop(const char *phase, const char *symbol, const char *reason,
                           const void *caller) {
    Dl_info info;
    memset(&info, 0, sizeof info);
    const char *image = "";
    if (caller != NULL && dladdr(caller, &info) != 0 && info.dli_fname != NULL) {
        image = info.dli_fname;
    }
    char escaped[512];
    json_escape(image, escaped, sizeof escaped);
    uintptr_t address = (uintptr_t)caller;
    int inside = caller != NULL && image_base != 0 && address >= image_base && address < image_end;
    uint64_t thread = 0;
    (void)pthread_threadid_np(pthread_self(), &thread);
    char body[1280];
    int length;
    int pid = (int)getpid();
    if (inside) {
        length = snprintf(body, sizeof body,
                          "{\"phase\":\"%s\",\"symbol\":\"%s\",\"image\":\"%s\","
                          "\"address\":\"0x%llx\",\"rva\":\"0x%llx\",\"reason\":\"%s\","
                          "\"thread\":%llu,\"pid\":%d}\n",
                          phase ? phase : "", symbol ? symbol : "", escaped,
                          (unsigned long long)address, (unsigned long long)(address - image_base),
                          reason ? reason : "", (unsigned long long)thread, pid);
    } else {
        length = snprintf(body, sizeof body,
                          "{\"phase\":\"%s\",\"symbol\":\"%s\",\"image\":\"%s\","
                          "\"address\":\"0x%llx\",\"rva\":null,\"reason\":\"%s\","
                          "\"thread\":%llu,\"pid\":%d}\n",
                          phase ? phase : "", symbol ? symbol : "", escaped,
                          (unsigned long long)address, reason ? reason : "",
                          (unsigned long long)thread, pid);
    }
    if (length < 0 || (size_t)length >= sizeof body) {
        _exit(2);
    }
    if (eu4_metal_events_write("stop.json", body) != 0) {
        _exit(2);
    }
}
