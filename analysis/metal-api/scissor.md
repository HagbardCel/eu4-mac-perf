# Scissor compatibility

`_GfxSetScissorRectangle` records a GL scissor box. It does not call Metal, and it does not enable scissor testing. Apple's requirements are the linked pages, not a copy of those pages:

- [setScissorRect:](https://developer.apple.com/documentation/metal/mtlrendercommandencoder/setscissorrect%28_%3A%29)
- [MTLScissorRect](https://developer.apple.com/documentation/metal/mtlscissorrect)
- [Validating your app's Metal API usage](https://developer.apple.com/documentation/xcode/validating-your-apps-metal-api-usage)

The installed headers are under `xcrun --sdk macosx --show-sdk-path`. A failed documentation fetch is an unresolved source, not a license to guess the contract.

## Observed GL box

The body at `0x15ed0aa` tail-calls `glScissor`. The four `int16_t` fields are `x1, y1, x2, y2`. At the call it passes:

- `gl_x = x1`
- `gl_y = viewport_height_at_master+0xac - y2`, as a 32-bit modular subtract read back with `memcpy` into `int32_t`
- `width = max(x2 - x1, 0)`
- `height = max(y2 - y1, 0)`

That box is fixed when the call returns. A later viewport change does not recompute it. `glScissor` does not enable `GL_SCISSOR_TEST`.

## Metal conversion

`MTLScissorRect` is top-left and must lie inside the render-pass attachment. Convert only against that attachment's height `H`, not against the viewport height captured above:

```c
int64_t metal_y = (int64_t)attachment_height - ((int64_t)gl_y + (int64_t)height);
```

Intersect in 64-bit arithmetic before any unsigned store. The helper returns `unset`, `empty`, or a nonempty `valid` rectangle inside the attachment. It does not invent an enable flag.

A later encoder, which this tree does not implement, must recover enablement separately:

- Scissor testing disabled: do not restrict drawing with the pending box.
- Enabled and `valid`: apply the returned rectangle.
- Enabled and `empty`: no pixels may be modified by scissor-affected draws or clears. `empty` is not permission to skip the scissor and draw or clear the whole attachment.
- Enabled and `unset`: the original box was never overridden. Recover the real GL initial box before using a full-target default.

## API validation

Host tests and reconstruction candidate tests set these before the first Metal device:

```text
MTL_DEBUG_LAYER=1
MTL_DEBUG_LAYER_ERROR_MODE=assert
```

Pass only a `valid` rectangle to `setScissorRect:`. Do not probe the validator with a deliberately invalid rectangle.
