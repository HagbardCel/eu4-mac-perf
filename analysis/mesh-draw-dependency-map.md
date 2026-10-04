# Mesh draw dependency map (static, pinned GOG 1.37.5)

Static reachability on SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Does not prove which paths executed in the Phase-C Venice frame without a new dynamic trace.

## Call reachability (map → mesh draw unit)

```text
CGraphics::RenderBuckets @ 0x10149cedc
  → CPdxMeshObject::RenderBuckets @ 0x1014c7da4
       → per-layer 24-byte descriptor
       → SFlushData (0x50) walk
       → mesh-type descriptor by LOD
       → 0xe8 draw subrecord loop
       → setup block ~0x14c81f3–0x14c8404
       → GfxDrawIndexed @ 0x14c8404 (landmark)
```

`CArray<SFlushData>::Append` @ `0x1014c72b0` constructs **0x50** parents only. **0xe8** records live in mesh-type static/instance data; separate loader/constructor analysis required ([`flush-data-re.md`](flush-data-re.md)).

## Per-draw inputs (subrecord loop entry → GfxDrawIndexed)

| Dependency | Role in loop | Predicate treatment |
|------------|--------------|---------------------|
| `_ObjectConstants` @ `0x102251ae8` | Updated @ `0x14c83d0`, bound @ `0x14c83f6`; uses parent transform + subrecord fields | Known-read parent/subrecord fields must match for setup elision; recompute is a barrier if inputs differ |
| Bone/animation constant buffer | Animated records may update bone CB earlier in loop | Hard barrier unless proven equal per record pair |
| `CEffectHandler::GetGfxEffect` | Effect pointer selection @ `0x14c81f3` | Effect pointer repeat enables **engine** partial skip (see below) |
| Texture IDs / `GfxSetTextures` | Three-ID resolve + six-slot conditional loop | Texture identity must match for setup elision |
| VBO / IBO / second VBO | Subrecord `+0x30`, `+0x38`, `+0x40` | Established equal fields in layout JSON |
| Mesh/object fields outside raw records | Mesh type supplies extra effect/texture IDs | Treat as barrier until mapped |
| Camera/global constants | Referenced indirectly via mesh object | Barrier unless traced to stable per-pair equality |
| Blend/depth/stencil/raster | Applied when effect pointer changes | Barrier across differing effect state |
| Opaque/transparent gate | Subrecord `+0x7d` vs layer flush array | Must match layer kind |

## Engine-side elision already present

From [`render-buckets.md`](render-buckets.md) and [`mesh-render-path.md`](mesh-render-path.md):

- **Effect pointer cache:** when consecutive subrecords share the same effect pointer, rasterizer/depth/blend/shader setup (`0x14c8204`–`0x14c8247`) is skipped.
- **Adjacent subrecord pointer / effect repeats:** some work avoided, but **`_ObjectConstants` still updated and bound every subrecord** before draw.

PR2 setup-elision must target work the engine **still performs** (object constants, texture resolves, buffer binds, six-slot loop), not re-implement effect-pointer caching.

## Removed-work inventory (candidate ROI)

When `setup_elision_eligible` fires, a mutating candidate could potentially skip:

| Work | Still runs today on repeat? | Notes |
|------|-----------------------------|-------|
| `GetGfxEffect` + RS/DS/blend/shader on same effect | Often skipped by engine | Low ROI if effect unchanged |
| `GfxSetTextures` + handler resolves | Usually still runs | Higher ROI if proven safe |
| VBO/IBO bind helpers | Usually still runs | Medium ROI |
| Six-slot texture loop | Conditional binds | Medium ROI |
| `_ObjectConstants` recompute + bind | **Always runs** | Highest ROI if equality proven |

Observer smoke (PR2) should measure `eligible_pair_hits` against this table before Stage 2 mutation.
