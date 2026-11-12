# EU4 M5 campaign

Reconstructs the loading-screen sprite draw that follows the M4 Simple text
draw and qualifies it against the authentic original-GL output.

## Run

```sh
python3 campaigns/eu4-m5/accept.py .
```

The checker is fail-closed. It verifies the committed evidence manifest and
policy, the authentic original-GL sprite reference (event, vertex, constants,
texture, shader sources, pre/post contribution), a fresh live-Metal replay of
the captured operation through the production encoder, exact composed pixel
equivalence, unsupported-state refusal, a corrupted-input negative control, the
M5 host tests, and the preserved M4 checker when the local M4 evidence is
present.

## Evidence

- [target selection](../../analysis/m5/target.md)
- [qualification report](../../analysis/m5/qualification-report.md)
- [comparison policy](../../analysis/m5/comparison-policy.json)
- [evidence manifest](../../analysis/m5/evidence-manifest.json)

Raw reference/replay payloads stay under `.local/m5/experiments/` and are
gitignored. The manifest pins their SHA-256 values and the exact reproduction
commands. A live engine launch is only needed to capture a new original-GL
reference; the replay and comparison are offline and deterministic.

## Live reach

The live engine now reaches the sprite draw. `_GfxBeginScene`,
`_GfxClearTexture` and the sprite blend descriptor are reconstructed in the
existing entry/state path; the probe records caller `0x14f6c1b`, shader
`sprite-map/asset-maincode-v0`, GPU status 4 and an engine-owned 3456×2234
backbuffer readback.

The live prefix composes the frame's Simple draw and the sprite and matches the
GL contribution count. The live prefix pre-draw still differs from the
original-GL pre-draw because the loading-screen background producer is not
reconstructed; live composed pixel equivalence is therefore a separate,
explicitly `false` result, not part of `m5_complete`. The exact seeded replay
remains the pixel qualification.
