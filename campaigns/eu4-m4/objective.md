# M4 objective — original-GL reference and real Metal presentation

Starting from the merged M3 Simple loading draw, capture the authentic
original-EU4 OpenGL output for the `_GfxDraw` at caller RVA `0xedfcd1`, capture
the corresponding reconstructed Metal output with identical live draw inputs,
compare them under a fixed policy, and present the reconstructed frame in the
actual EU4 window with independent visible-window evidence.

`qualified` requires every acceptance gate. A missing reference, missing GPU
completion, missing presentation evidence or missing provenance is not a pass.
