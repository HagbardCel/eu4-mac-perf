# Graphics call-graph integration (maintainer)

Run after PR #48/#49 inventory is present under `.local/eu4/<sha>/exports/<revision>/`.

```bash
export GHIDRA_INSTALL=...  # not required for graph analysis
python3 -m reverse_engineering.graph_analysis audit --baseline-revision <rev>
python3 -m reverse_engineering.graph_analysis extract --baseline-revision <rev> --write-report
```

Record in a dated memo (`graphics-call-graph-YYYYMMDD.md`):

- Pinned SHA-256 and `baseline_revision`
- `inventory_bundle_digest` match from `call-graph-audit.json`
- Gfx reconcile histogram (48 draw sites)
- `boundary_conclusion` and `unknown_target_risk` from both cuts
- Counts of `direct_supported_bypass_candidate` vs `indeterminate_after_cut` per endpoint family
- Top 10 `decompile_priorities` RVAs

Do not commit `exports/.../logs/` verbatim; promote only reviewed conclusions if needed.
