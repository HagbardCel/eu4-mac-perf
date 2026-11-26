/* Exercise the actual in-process function cache with a CPU-only fake compiler. */
#include "../benchmark/eu4_gfx_frame_replay.mm"

#include <assert.h>

@interface MockShaderCompiler : NSObject
@property NSMutableArray *calls;
@end

@implementation MockShaderCompiler
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options
                     error:(NSError **)error {
    NSMutableDictionary *call = [@{
        @"source": source, @"fast_math": @(options.fastMathEnabled),
        @"language": @(options.languageVersion),
    } mutableCopy];
    [self.calls addObject:call];
    return (id)self;
}
- (id)newFunctionWithName:(NSString *)entry {
    self.calls.lastObject[@"entry"] = entry;
    return [NSObject new];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        assert(argc == 2);
        EU4MetalReplayState *state = [EU4MetalReplayState new];
        MockShaderCompiler *compiler = [MockShaderCompiler new];
        compiler.calls = [NSMutableArray array];
        state.device = (id<MTLDevice>)compiler;
        state.functions = [NSMutableDictionary dictionary];
        g_state = state;

        NSDictionary *stage = @{@"msl_file": @"test.metal",
                                @"msl_entry": @"vertex_main", @"stage": @"vertex"};
        NSString *shader_dir = @(argv[1]);
        BOOL fast_math = NO;
        assert(resolve_shader_fast_math(NULL, NULL, &fast_math) == nil && fast_math);
        state.shader_fast_math = fast_math;
        NSString *first_key = nil;
        id first = function_for(shader_dir, stage, &first_key, NULL, NULL);
        assert(first != nil && [first_key hasSuffix:@"#vertex_main#vertex#fastmath-on"]);

        assert(resolve_shader_fast_math("1", NULL, &fast_math) == nil && fast_math);
        state.shader_fast_math = fast_math;
        NSString *second_key = nil;
        assert(function_for(shader_dir, stage, &second_key, NULL, NULL) == first);
        assert([first_key isEqual:second_key] && compiler.calls.count == 1);

        assert(resolve_shader_fast_math("0", NULL, &fast_math) == nil && !fast_math);
        state.shader_fast_math = fast_math;
        id precise = function_for(shader_dir, stage, &second_key, NULL, NULL);
        assert(precise != nil && precise != first);
        assert([second_key hasSuffix:@"#vertex_main#vertex#fastmath-off"]);
        assert(function_for(shader_dir, stage, NULL, NULL, NULL) == precise);
        NSArray *expected = @[
            @{@"source": @"mock MSL", @"fast_math": @YES,
              @"entry": @"vertex_main", @"language": @(MTLLanguageVersion2_1)},
            @{@"source": @"mock MSL", @"fast_math": @NO,
              @"entry": @"vertex_main", @"language": @(MTLLanguageVersion2_1)},
        ];
        assert([compiler.calls isEqual:expected]);
        assert(resolve_shader_fast_math("01", NULL, &fast_math) != nil);
        assert(resolve_shader_fast_math("1", "1", &fast_math) != nil);
        g_state = nil;
    }
    return 0;
}
