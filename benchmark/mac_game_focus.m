#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc,char **argv) {
    if (argc!=3) return 64;
    char *end=NULL;
    long requested=strtol(argv[2],&end,10);
    if (!end || *end || requested<=0 || requested>INT_MAX) return 64;
    @autoreleasepool {
        pid_t pid=(pid_t)requested;
        NSRunningApplication *app=[NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        if (!app || app.terminated) return 2;
        if (!strcmp(argv[1],"activate")) {
            if (![app activateWithOptions:NSApplicationActivateIgnoringOtherApps]) return 3;
        } else if (!strcmp(argv[1],"check")) {
            NSRunningApplication *front=[NSWorkspace sharedWorkspace].frontmostApplication;
            if (!front || front.processIdentifier!=pid) return 4;
        } else if (!strcmp(argv[1],"park") || !strcmp(argv[1],"interior")) {
            NSRunningApplication *front=[NSWorkspace sharedWorkspace].frontmostApplication;
            if (!front || front.processIdentifier!=pid) return 4;
            CGRect bounds=CGDisplayBounds(CGMainDisplayID());
            if (!strcmp(argv[1],"park")) {
                CGPoint center=CGPointMake(CGRectGetMidX(bounds),CGRectGetMidY(bounds));
                if (CGWarpMouseCursorPosition(center)!=kCGErrorSuccess) return 6;
            } else {
                CGEventRef event=CGEventCreate(NULL);
                if (!event) return 7;
                CGPoint point=CGEventGetLocation(event);
                CFRelease(event);
                CGRect interior=CGRectInset(bounds,0.15*bounds.size.width,0.15*bounds.size.height);
                if (!CGRectContainsPoint(interior,point)) return 8;
            }
        } else if (!strcmp(argv[1],"terminate")) {
            if (![app terminate]) return 5;
        } else return 64;
    }
    return 0;
}
