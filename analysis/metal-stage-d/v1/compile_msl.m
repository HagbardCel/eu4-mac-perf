#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>

// Compiles one MSL file with the Metal compiler in the OS. Not a pipeline.
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: compile_msl file.metal\n");
            return 2;
        }
        NSError *read_error = nil;
        NSString *source = [NSString stringWithContentsOfFile:@(argv[1])
                                                     encoding:NSUTF8StringEncoding
                                                        error:&read_error];
        if (source == nil) {
            fprintf(stderr, "%s\n", read_error.localizedDescription.UTF8String);
            return 1;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            fprintf(stderr, "no metal device\n");
            return 1;
        }
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
        if (library == nil) {
            fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        printf("%s\n", [library.functionNames componentsJoinedByString:@" "].UTF8String);
        return 0;
    }
}
