#import "eu4_metal_geometry_view.h"
#import "eu4_gfx_init.h"

@implementation EU4MetalChildView

- (void)syncDrawableGeometry {
    CAMetalLayer *layer = self.metalLayer;
    if (layer == nil) {
        return;
    }
    NSRect backing = [self convertRectToBacking:self.bounds];
    if (backing.size.width <= 0 || backing.size.height <= 0) {
        return;
    }
    uint32_t width = (uint32_t)backing.size.width;
    uint32_t height = (uint32_t)backing.size.height;
    if (width == 0 || height == 0) {
        return;
    }
    CGSize current = layer.drawableSize;
    eu4_gfx_physical_geometry_state state;
    int have = eu4_gfx_physical_geometry_snapshot(&state);
    if (have && state.valid && !state.transition_in_progress && state.width == width &&
        state.height == height && current.width == (CGFloat)width &&
        current.height == (CGFloat)height) {
        /* Identical reobserve: no publication, no content generation change. */
        return;
    }
    /* Phase 1: the renderer must stop presenting before the layer changes. */
    eu4_gfx_publish_geometry_transition(width, height);
    /* Phase 2: resize the layer, then commit the epoch the renderer reconciles. */
    layer.drawableSize = CGSizeMake(width, height);
    eu4_gfx_publish_physical_geometry(width, height);
}

- (void)setFrameSize:(NSSize)newSize {
    [super setFrameSize:newSize];
    [self syncDrawableGeometry];
}

- (void)layout {
    [super layout];
    [self syncDrawableGeometry];
}

- (void)viewDidChangeBackingProperties {
    [super viewDidChangeBackingProperties];
    [self syncDrawableGeometry];
}

@end
