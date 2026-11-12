# eu4-m4

M4 acceptance for the Simple loading draw: authentic original-OpenGL
reference, corresponding reconstructed Metal output, fixed-policy comparison,
real-window presentation evidence and autonomous DSH provenance.

```sh
python3 campaigns/eu4-m4/accept.py <workspace>
```

The checker prints one JSON object with a `pass` / `fail` / `inconclusive`
verdict and per-gate details. `pass` is the only M4 qualification. Evidence
artifacts are large and stay under `.local/m4/experiments/` (gitignored); the
committed `analysis/m4/evidence-manifest.json` pins their SHA-256 values and is
itself pinned by `evidence_manifest_sha256` in `campaign.json`.

A `presentation:visible` failure is deliberately `inconclusive`, not a pass: a
completed `presentDrawable` and a drawable readback are not proof that the
frame reached the window.
