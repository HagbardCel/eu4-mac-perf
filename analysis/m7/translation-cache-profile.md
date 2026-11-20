# Translation cache and tool-identity profile

Measured 2026-11-20 in the `b4` worktree on macOS (arm64, Apple clang,
Python 3.14.5).  This is an offline measurement; no EU4 launch and no GPU
replay was involved.  It records the cost of the identity work added to the
cross-frame translation cache so a later performance change is based on data,
not on an assumption.

## Command

The measurement imported the production helpers directly and repeated each
operation 20-30 times after filesystem warm-up:

```sh
python3.14 - <<'PY'
from automation import m6_frame
# repeat m6_frame._translator_identity(), _sha256(tool), and
# _translation_cache_valid()/_copy_translation_cache() over a synthetic
# 17-program (33-stage, 102-artifact) cache.
PY
```

Tool binaries resolved to `/opt/homebrew/bin/glslangValidator` (16.6.0,
160,448 bytes) and `/opt/homebrew/bin/spirv-cross` (1.4.363.0, 3,574,376
bytes).  The translator is `automation/gfx_shader_translate.py` (31,961
bytes).

## Measurements

| Operation | Median | Range |
|---|---:|---:|
| `_translator_identity()` (which + hash translator and both tools) | 29.7 ms | 25.6-40.5 ms |
| `shutil.which("glslangValidator")` alone | 9.9 ms | - |
| `shutil.which("spirv-cross")` alone | 9.6 ms | - |
| SHA-256 of translator (31,961 B) | 0.13 ms | - |
| SHA-256 of `glslangValidator` (160,448 B) | 0.17 ms | - |
| SHA-256 of `spirv-cross` (3,574,376 B) | 2.89 ms | - |
| `_translation_cache_valid()` over 102 artifacts | 8.1 ms | 8.1-15.3 ms |
| `_copy_translation_cache()` over 102 artifacts | 81.5 ms | 81.5-121.4 ms |

A warm hit therefore costs roughly 30 ms of identity resolution plus about
90 ms of validation and copy in this synthetic cache.  A cold translation
also invokes glslang and SPIRV-Cross once per stage; those compiler
invocations dominate the operation and were not included in this table.

## Finding and decision

PATH resolution (two `shutil.which` calls) accounts for about 20 ms of the
30 ms identity cost; hashing the 3.7 MB of tool binaries accounts for only
about 3 ms.  The cost is bounded and paid once per `translate` invocation,
not once per stage or draw.

No tool-identity or PATH memoisation was added.  A process-lifetime cache
would have to be invalidated when a tool is replaced, and the measured cost
does not justify that new state.  Re-measure before changing this: a
continuous campaign that translates many frames per second would change the
tradeoff, and the native replay session now also validates effective shader
bytes per lookup.
