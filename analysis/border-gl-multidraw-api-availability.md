# Gate 0 — `glMultiDrawElementsBaseVertex` API viability

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876…`)  
**Verdict:** **RUNTIME-ASSERT** (proceed to PR C fail-closed verification)

## Evidence

| Source | Finding |
|--------|---------|
| [frame-model-draw-api.json](frame-model-draw-api.json) | Symbol catalog includes `glMultiDrawElementsBaseVertex` in macOS OpenGL framework inventory used by offline frame model |
| [mesh-render-path.md](mesh-render-path.md) | Border path tail-jumps from `GfxDrawIndexed` to `glDrawElementsBaseVertex` when base vertex ≠ 0 |
| Executable `nm -u` | No direct link of GL entry points (runtime `dlsym` / framework dispatch expected) |
| [frame-model-backend.json](frame-model-backend.json) | `NSOpenGLContext` / `NSOpenGLPixelFormat` used; **compatibility profile not statically proven** |

Imports alone do **not** prove the active gameplay context exposes base-vertex multidraw.

## Outcomes (plan taxonomy)

- **PROVEN:** Not established offline — no pinned context-creation disassembly tying pixel format to GL 3.2+ core with `GL_ARB_draw_elements_base_vertex` / macOS 10.7+ API.
- **DISPROVEN:** No evidence the game forbids the call; macOS OpenGL 4.1 stack lists the entry point in frame-model inventories.
- **RUNTIME-ASSERT:** Static RE + inventories make the call **plausible**; PR C must `dlsym` / probe before enabling mutation (Gate 6).

## PR C requirement

Capability-2 dylib resolves `glMultiDrawElementsBaseVertex` at install time. If resolution fails, mutation remains disabled (pass-through only); manifest records `border_api_runtime_assert: failed`.
