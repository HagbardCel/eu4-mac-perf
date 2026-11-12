/* List one application's layer-0 windows as JSON.
 *
 *   eu4_window_list <pid>
 *
 * Used with `screencapture -l <window_id>` for independent visible-window
 * evidence. The list comes from the window server, not the game process. */
#import <ApplicationServices/ApplicationServices.h>
#import <CoreFoundation/CoreFoundation.h>
#import <stdio.h>
#import <stdlib.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <pid>\n", argv[0]);
        return 2;
    }
    int pid = atoi(argv[1]);
    if (pid <= 0) {
        fprintf(stderr, "invalid pid\n");
        return 2;
    }
    CFArrayRef windows = CGWindowListCopyWindowInfo(
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID);
    if (windows == NULL) {
        fprintf(stderr, "window list unavailable\n");
        return 3;
    }
    int largest_id = 0;
    double largest_area = 0;
    int index = 0;
    for (; index < (int)CFArrayGetCount(windows); index++) {
        CFDictionaryRef info =
            (CFDictionaryRef)CFArrayGetValueAtIndex(windows, index);
        CFNumberRef owner =
            (CFNumberRef)CFDictionaryGetValue(info, kCGWindowOwnerPID);
        CFNumberRef layer =
            (CFNumberRef)CFDictionaryGetValue(info, kCGWindowLayer);
        CFNumberRef number =
            (CFNumberRef)CFDictionaryGetValue(info, kCGWindowNumber);
        CFDictionaryRef bounds =
            (CFDictionaryRef)CFDictionaryGetValue(info, kCGWindowBounds);
        int owner_pid = 0, layer_value = 0, window_id = 0;
        if (owner == NULL || layer == NULL || number == NULL || bounds == NULL ||
            !CFNumberGetValue(owner, kCFNumberIntType, &owner_pid) ||
            !CFNumberGetValue(layer, kCFNumberIntType, &layer_value) ||
            !CFNumberGetValue(number, kCFNumberIntType, &window_id)) {
            continue;
        }
        if (owner_pid != pid) {
            continue;
        }
        CGRect rect;
        if (!CGRectMakeWithDictionaryRepresentation(bounds, &rect)) {
            continue;
        }
        if (rect.size.width < 16 || rect.size.height < 16) {
            continue;
        }
        CFStringRef name_ref =
            (CFStringRef)CFDictionaryGetValue(info, kCGWindowName);
        CFNumberRef alpha_ref =
            (CFNumberRef)CFDictionaryGetValue(info, kCGWindowAlpha);
        CFBooleanRef onscreen_ref =
            (CFBooleanRef)CFDictionaryGetValue(info, kCGWindowIsOnscreen);
        char name[256];
        name[0] = 0;
        if (name_ref != NULL) {
            CFStringGetCString(name_ref, name, sizeof name, kCFStringEncodingUTF8);
        }
        double alpha = 1.0;
        if (alpha_ref != NULL) {
            CFNumberGetValue(alpha_ref, kCFNumberDoubleType, &alpha);
        }
        int onscreen = onscreen_ref != NULL && CFBooleanGetValue(onscreen_ref);
        printf("{\"window_id\":%d,\"layer\":%d,\"width\":%d,\"height\":%d,"
               "\"x\":%d,\"y\":%d,\"alpha\":%.3f,\"onscreen\":%s,"
               "\"name\":\"%s\"}\n",
               window_id, layer_value, (int)rect.size.width, (int)rect.size.height,
               (int)rect.origin.x, (int)rect.origin.y, alpha,
               onscreen ? "true" : "false", name);
        double area = rect.size.width * rect.size.height;
        if (area > largest_area) {
            largest_area = area;
            largest_id = window_id;
        }
    }
    CFRelease(windows);
    if (largest_id == 0) {
        fprintf(stderr, "no layer-0 window for pid %d\n", pid);
        return 4;
    }
    printf("{\"largest_window_id\":%d}\n", largest_id);
    return 0;
}
