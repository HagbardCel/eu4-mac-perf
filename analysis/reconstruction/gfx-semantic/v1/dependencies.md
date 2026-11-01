# Dependencies

Callees inside the 92 are not repeated here. A name is external when it is not one of those 92.
Bodies of external functions are not reconstructed in this tree.

The known-root surface is larger than the 92. `GfxCreateTexture`, the `GetOpenGLContext*` readers, and `BuildGLSL` are already called out in [boundary.md](../../../../analysis/metal-census/v1/boundary.md).

| callee | called from |
| --- | --- |
| `/usr/lib/libc++.1.dylib::operator.delete` | `_GfxBuildAndAddPixelShader`, `_GfxBuildAndAddVertexShader`, `_GfxDestroyBlendState`, `_GfxDestroyConstantBuffer`, `_GfxDestroyDepthStencilState`, `_GfxDestroyIndexBuffer`, `_GfxDestroyRasterizerState`, `_GfxDestroySampler`, `_GfxDestroyShader`, `_GfxDestroyTexture`, `_GfxDestroyVertexBuffer`, `_GfxInit`, `_GfxShutdown`, `_GfxWriteScreenshot` |
| `/usr/lib/libc++.1.dylib::operator.delete[]` | `_GfxDestroyConstantBuffer`, `_GfxWriteTextureAsTGA` |
| `/usr/lib/libc++.1.dylib::operator.new` | `_GfxCreateBlendState`, `_GfxCreateConstantBuffer`, `_GfxCreateDepthStencilState`, `_GfxCreateIndexBuffer`, `_GfxCreateRasterizerState`, `_GfxCreateRenderTargetTexture`, `_GfxCreateSampler`, `_GfxCreateShader`, `_GfxCreateTexture`, `_GfxCreateVertexBuffer`, `_GfxInit` |
| `/usr/lib/libc++.1.dylib::operator.new[]` | `_GfxAddVertexLayout`, `_GfxCreateConstantBuffer`, `_GfxWriteTextureAsTGA` |
| `BuildGLSL` | `_GfxBuildAndAddPixelShader`, `_GfxBuildAndAddVertexShader` |
| `CPdxLogFileAndLine::CPdxLogFileAndLine` | `_GfxBuildAndAddPixelShader`, `_GfxBuildAndAddVertexShader` |
| `CPdxLogFileAndLine::operator()` | `_GfxBuildAndAddPixelShader`, `_GfxBuildAndAddVertexShader` |
| `ClearGLSLBuilderCache` | `_GfxClearBuilderCache` |
| `GetDefaultBlendState` | `_GfxInit` |
| `GetDefaultDepthStencil` | `_GfxInit` |
| `GetDefaultRasterizer` | `_GfxInit` |
| `GetOpenGLContextBlendState` | `_GfxInit`, `_GfxOnResetMasterContext`, `_GfxReset` |
| `GetOpenGLContextDepthStencilState` | `_GfxInit`, `_GfxOnResetMasterContext`, `_GfxReset` |
| `GetOpenGLContextRasterizerState` | `_GfxInit`, `_GfxOnResetMasterContext`, `_GfxReset` |
| `GetOpenGLCubemapFace` | `_GfxInitializeTexture` |
| `GetOpenGLPrimitiveType` | `_GfxSetPrimitiveType` |
| `GetOpenGLTextureFormat` | `_GfxInitializeRenderTargetTexture`, `_GfxInitializeTexture` |
| `GetOpenGLTextureMagFilterType` | `_GfxCreateSampler` |
| `GetOpenGLTextureMinFilterype` | `_GfxCreateSampler` |
| `GetOpenGLTextureMipFilterype` | `_GfxCreateSampler` |
| `GetOpenGLTextureWrapType` | `_GfxCreateSampler` |
| `GfxCreateTexture` | `_GfxInit` |
| `GfxWriteBufferAsPNG` | `_GfxWriteScreenshot` |
| `GfxWriteBufferAsTGA` | `_GfxWriteTextureAsTGA` |
| `Gfx_Util::GetBitsPerPixel` | `_GfxInitializeTexture` |
| `Gfx_Util::GetBytesPerPixel` | `_GfxInitializeRenderTargetTexture` |
| `Gfx_Util::IsBlockCompressed` | `_GfxInitializeTexture` |
| `InitOpenGLConversionTables` | `_GfxInit` |
| `InternalSetTextureAndSamplerState` | `_GfxSetTextures` |
| `LogShaderError` | `_GfxAddPixelShader`, `_GfxAddVertexShader` |
| `SOpenGLBlendState::Init` | `_GfxCreateBlendState`, `_GfxInit` |
| `SOpenGLDepthStencilState::Init` | `_GfxCreateDepthStencilState`, `_GfxInit` |
| `SOpenGLRasterizerState::Init` | `_GfxCreateRasterizerState`, `_GfxInit` |
| `SShaderOpenGL::EnableVertexAttribArrays` | `_GfxDraw`, `_GfxDrawIndexed`, `_GfxDrawIndexedInstanced` |
| `SShaderOpenGL::LinkProgram` | `_GfxLinkShader` |
| `SShaderOpenGL::SetAll` | `_GfxSetShader` |
| `SShaderOpenGL::UnloadAll` | `_GfxDestroyShader` |
| `_AtomicFetchAndIncrement32` | `_GfxCreateSampler` |
| `_SDL_CreateWindow` | `_GfxGetGraphicsSettings` |
| `_SDL_DestroyWindow` | `_GfxGetGraphicsSettings` |
| `_SDL_GL_CreateContext` | `_GfxGetGraphicsSettings`, `_GfxInit` |
| `_SDL_GL_DeleteContext` | `_GfxGetGraphicsSettings`, `_GfxShutdown` |
| `_SDL_GL_GetAttribute` | `_GfxInit` |
| `_SDL_GL_SetAttribute` | `_GfxGetGraphicsSettings` |
| `_SDL_GL_SetSwapInterval` | `_GfxGetGraphicsSettings`, `_GfxInit` |
| `_SDL_GL_SwapWindow` | `_GfxPresent` |
| `_SDL_GetDisplayMode` | `_GfxGetAdapterDisplayMode` |
| `_SDL_GetError` | `_GfxInit` |
| `_SDL_GetNumDisplayModes` | `_GfxGetAdapterModeCount` |
| `_SDL_GetWindowDisplayIndex` | `_GfxGetAdapterDisplayMode`, `_GfxGetAdapterModeCount` |
| `_SDL_GetWindowFromID` | `_GfxInit` |
| `_SDL_GetWindowSize` | `_GfxInit` |
| `_SDL_InitSubSystem` | `_GfxGetGraphicsSettings` |
| `_SDL_QuitSubSystem` | `_GfxGetGraphicsSettings` |
| `_SDL_WasInit` | `_GfxGetGraphicsSettings` |
| `_glAlphaFunc` | `_GfxSetBlendState` |
| `_glBegin` | `_GfxDraw2dLines` |
| `_glBindTexture` | `_GfxGenerateMipMaps`, `_GfxGetTextureBuffer`, `_GfxInitializeRenderTargetTexture`, `_GfxInitializeTexture`, `_GfxResolve`, `_GfxUpdateTexture`, `_GfxUpdateTextureRectangle`, `_GfxWriteTextureAsTGA` |
| `_glBlendFunc` | `_GfxSetBlendState` |
| `_glClear` | `_GfxClear` |
| `_glClearColor` | `_GfxClear` |
| `_glColor4f` | `_GfxDraw2dLines` |
| `_glColorMask` | `_GfxClear`, `_GfxSetBlendState` |
| `_glCopyTexSubImage2D` | `_GfxResolve` |
| `_glCullFace` | `_GfxSetRasterizerState` |
| `_glDeleteTextures` | `_GfxClearTexture` |
| `_glDepthFunc` | `_GfxSetDepthStencilState` |
| `_glDepthMask` | `_GfxClear`, `_GfxSetDepthStencilState` |
| `_glDisable` | `_GfxClear`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState` |
| `_glDrawArrays` | `_GfxDraw` |
| `_glDrawElements` | `_GfxDrawIndexed` |
| `_glEnable` | `_GfxClear`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState` |
| `_glEnd` | `_GfxDraw2dLines` |
| `_glFrontFace` | `_GfxSetRasterizerState` |
| `_glGenTextures` | `_GfxInitializeRenderTargetTexture`, `_GfxInitializeTexture` |
| `_glGetError` | `_GfxAddPixelShader`, `_GfxAddVertexShader` |
| `_glGetFloatv` | `_GfxGetGraphicsSettings` |
| `_glGetIntegerv` | `_GfxGetGraphicsSettings`, `_GfxInit` |
| `_glGetString` | `_GfxInit` |
| `_glGetTexImage` | `_GfxGetTextureBuffer`, `_GfxWriteTextureAsTGA` |
| `_glLineWidth` | `_GfxDraw2dLines` |
| `_glLoadIdentity` | `_GfxDraw2dLines` |
| `_glMatrixMode` | `_GfxDraw2dLines` |
| `_glOrtho` | `_GfxDraw2dLines` |
| `_glPixelStorei` | `_GfxInitializeTexture`, `_GfxUpdateTexture`, `_GfxUpdateTextureRectangle` |
| `_glPolygonMode` | `_GfxSetRasterizerState` |
| `_glReadPixels` | `_GfxWriteScreenshot` |
| `_glScissor` | `_GfxClear`, `_GfxSetScissorRectangle` |
| `_glStencilFunc` | `_GfxSetDepthStencilState` |
| `_glStencilMask` | `_GfxClear`, `_GfxSetDepthStencilState` |
| `_glStencilOp` | `_GfxSetDepthStencilState` |
| `_glTexImage2D` | `_GfxInitializeRenderTargetTexture`, `_GfxInitializeTexture`, `_GfxUpdateTexture` |
| `_glTexSubImage2D` | `_GfxUpdateTextureRectangle` |
| `_glVertex2f` | `_GfxDraw2dLines` |
| `_glViewport` | `_GfxResolveTexture`, `_GfxSetRenderTarget`, `_GfxSetViewPort` |
| `_glewGetErrorString` | `_GfxGetGraphicsSettings`, `_GfxInit` |
| `_glewInit` | `_GfxGetGraphicsSettings`, `_GfxInit` |
| `_memcpy` | `_GfxUpdateConstantBuffer` |
| `std::vector<char,std::allocator<char>>::__append` | `_GfxWriteScreenshot` |
