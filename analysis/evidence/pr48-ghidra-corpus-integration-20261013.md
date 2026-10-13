# PR #48 Ghidra corpus integration gate (partial)

**Status:** `partial_complete`  
**Integration run commit:** `8303e0b62936f12c3252aa14cfc5034ecfae119b`  
**Baseline revision:** `auto-v1-20261013`  
**Binary SHA-256:** `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`

Artifacts below were verified on a maintainer machine. Paths under `.local/eu4/<sha>/` and `~/GhidraEU4/<sha>/` are **local only** (not committed to git).

## Acceptance checklist

| Criterion | Result | Notes |
|-----------|--------|-------|
| Binary import + metadata | **pass** | `ghidra_project.json` → `project_dir` `/Users/fabian/GhidraEU4/b3d38876…/ghidra-project` (no dot path components); `program_metadata.json` has `compiler_spec_id: gcc` |
| Smoke decompile (3 anchors) | **pass** | `logs/smoke-decompile.json`: all three needles `success: true` (no pseudocode in smoke artifact) |
| Anchor pseudocode export | **pass** | `logs/anchor-pseudocode.jsonl`: 3 lines, all `status: success`, nonempty `pseudocode` |
| Inventory bundle | **pass** | `functions.jsonl` ~31.2 MiB, `call_edges.jsonl` ~107.4 MiB, `coverage.json` 393 B |
| Manifest batch | **pass** | `decompiled/batch_manifest.json`: 84 779 eligible functions, `--batch-size 25` |
| One batch decompilation | **pass** | `batch-02666.jsonl` (25 RVAs, includes anchor `0x14c7da4`); `validate_batch_file` OK; 25/25 `success` with nonempty pseudocode |
| SQLite index | **pass** | `functions` 85 826; `call_edges` 544 566; `pseudocode` 25 |
| FTS pseudocode search | **pass** | `search text RenderBuckets` → hit `0x00000000014c7da4` (term from batch pseudocode) |
| Call graph navigation | **pass** | `search callees 0x0000000000009d00` → internal edges (e.g. callee `0x0000000000a4804c`) |

## Key counts

- Manifest batches: 3 391 (`batch-00000` … `batch-03390`); **one** batch exported for this gate (`batch-02666`).
- Anchor entry RVAs (from inventory demangled names): `SShaderOpenGL::SetAll` `0x15ebc40`, `CPdxMeshObject::RenderBuckets` `0x14c7da4`, `CEU3GraphicalMap::Render` `0xed4ac8`.
- Ghidra: Homebrew `12.1.4` (`GHIDRA_INSTALL=/opt/homebrew/Cellar/ghidra/12.1.4/libexec`), `JAVA_HOME` OpenJDK 21 for headless.

## Anchor pseudocode excerpt (`CPdxMeshObject::RenderBuckets`)

```
/* CPdxMeshObject::RenderBuckets(CGraphics*, GfxDeferredContextGFX*, CCamera const*, int, bool,
   bool) */

void CPdxMeshObject::RenderBuckets
               (CGraphics *param_1,GfxDeferredContextGFX *param_2,CCamera *param_3,int param_4,
               bool param_5,bool param_6)

{
  undefined4 *puVar1;
  long lVar2;
  undefined8 uVar3;
```

## FTS snippet (`search text RenderBuckets`)

```
0x00000000014c7da4  …CPdxMeshObject::>>>RenderBuckets<<<(CGraphics*, GfxDeferredContextGFX*…
```

## Commands exercised (representative)

```bash
export GHIDRA_INSTALL=/opt/homebrew/Cellar/ghidra/12.1.4/libexec
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home

python3 -m reverse_engineering.corpus manifest --baseline-revision auto-v1-20261013 --batch-size 25
# headless ExportDecompilation.java → logs/anchor-pseudocode.jsonl (3 RVAs)
# headless ExportDecompilation.java → decompiled/batch-02666.jsonl (+ publish_batch_staging)

python3 -m reverse_engineering.corpus index --baseline-revision auto-v1-20261013
python3 -m reverse_engineering.search --baseline-revision auto-v1-20261013 symbol RenderBuckets
python3 -m reverse_engineering.search --baseline-revision auto-v1-20261013 text RenderBuckets
python3 -m reverse_engineering.search --baseline-revision auto-v1-20261013 callees 0x0000000000009d00
```

## Conclusion

The pinned EU IV executable was imported and the renderer anchors decompiled successfully. A single manifest batch was exported, validated, and indexed. Symbol, pseudocode, caller, and callee queries were exercised successfully. Full-corpus decompilation and authoritative renderer census remain follow-up work.
