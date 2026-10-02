# Held-out source projection (CI replay)

Large passive traces (`results/**/trace.bin`) stay **local/gitignored**. This directory holds a
**small derived projection** sufficient to replay:

```text
terrain_odd_ordinal_within_frame_v1 → dbb0af0e7964777b7ab9f0fecde068692377719bde1670c2a082ecbb26281503
```

Regenerate from a Mac checkout with local trace:

```bash
python3 benchmark/export_held_out_source_projection.py
```

Portable CI verifies the projection via `tests/test_frame_model_held_out_fixture.py`.
