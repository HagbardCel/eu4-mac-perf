#ifndef EU4_METAL_GEOMETRY_VIEW_H
#define EU4_METAL_GEOMETRY_VIEW_H

#import <AppKit/AppKit.h>
#import <QuartzCore/CAMetalLayer.h>

/* Production Metal child view shared by eu4_metal_boot.m and the AppKit host.
   Tracks the parent content bounds and backing scale, and drives the two-phase
   physical geometry publication. Main thread only. */
@interface EU4MetalChildView : NSView
@property (nonatomic, weak) CAMetalLayer *metalLayer;
- (void)syncDrawableGeometry;
@end

#endif
