# Scissor compatibility

[Project-derived] `_GfxSetScissorRectangle` records a GL scissor box. It does not call Metal, and it does not enable scissor testing. The pages below are the Apple sources. This note does not copy them:

- [setScissorRect:](https://developer.apple.com/documentation/metal/mtlrendercommandencoder/setscissorrect(_:))
- [MTLScissorRect](https://developer.apple.com/documentation/metal/mtlscissorrect)
- [Validating your app's Metal API usage](https://developer.apple.com/documentation/xcode/validating-your-apps-metal-api-usage)

The installed headers are under `xcrun --sdk macosx --show-sdk-path`. [Project-derived] A failed documentation fetch is an unresolved source, not a license to guess the contract.

## Provenance

Reviewed 2026-11-05 against macOS SDK 27.0. The active developer directory is Command Line Tools. `xcodebuild` is unavailable, and no Apple documentation archive is installed. Header `Metal.framework/Headers/MTLRenderCommandEncoder.h` was read. The [setScissorRect:](https://developer.apple.com/documentation/metal/mtlrendercommandencoder/setscissorrect(_:)) page was opened the same day. The attachment-bounds and upper-left sentences are from that page. The header does not state the attachment bound. The validation guide linked above was not re-opened for this review.

## Observed GL box

[Project-derived] The body at `0x15ed0aa` tail-calls `glScissor`. The four `int16_t` fields are `x1, y1, x2, y2`. At the call it passes:

- `gl_x = x1`
- `gl_y = viewport_height_at_master+0xac - y2`, as a 32-bit modular subtract read back with `memcpy` into `int32_t`
- `width = max(x2 - x1, 0)`
- `height = max(y2 - y1, 0)`

[Project-derived] That box is fixed when the call returns. A later viewport change does not recompute it. `glScissor` does not enable `GL_SCISSOR_TEST`.

## Metal conversion

[SDK] `MTLScissorRect` is `x`, `y`, `width`, and `height`, each an `NSUInteger`. [SDK] `setScissorRect:` discards fragments outside the rectangle. [Apple docs] The rectangle needs to lie completely within the current render attachment. [Apple docs] The default rectangle matches that attachment, and its origin is the upper left.

[Project-derived] Convert only against that attachment's height, not against the viewport height captured above. The helper is [`eu4_gfx_scissor_for_attachment`](../../benchmark/eu4_gfx_init.h):

```c
int64_t metal_y = (int64_t)attachment_height - ((int64_t)gl_y + (int64_t)height);
```

[Project-derived] Intersect in 64-bit arithmetic before any unsigned store. The helper returns `unset`, `empty`, or a nonempty `valid` rectangle inside the attachment. It does not invent an enable flag.

[Project-derived] A later encoder, which this tree does not implement, must recover enablement separately:

- Scissor testing disabled: do not restrict drawing with the pending box.
- Enabled and `valid`: apply the returned rectangle.
- Enabled and `empty`: no pixels may be modified by scissor-affected draws or clears. `empty` is not permission to skip the scissor and draw or clear the whole attachment.
- Enabled and `unset`: the original box was never overridden. Recover the real GL initial box before using a full-target default.

## API validation

[Project-derived] Host tests and reconstruction candidate tests set these before the first Metal device:

```text
MTL_DEBUG_LAYER=1
MTL_DEBUG_LAYER_ERROR_MODE=assert
```

[Project-derived] Pass only a `valid` rectangle to `setScissorRect:`. Do not probe the validator with a deliberately invalid rectangle. The host check is [`tests/gfx_init_host.mm`](../../tests/gfx_init_host.mm).
