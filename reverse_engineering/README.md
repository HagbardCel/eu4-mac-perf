# Reverse engineering

Recover and understand the original EU IV macOS implementation. **Phase 1 (full Ghidra auto-analysis and local searchable corpus)** is planned as restructuring PR D; automation and reports will land in this tree.

## Local corpus (not in Git)

Legitimately obtained game binaries and derived artifacts stay outside the public repository:

```text
.local/eu4/<binary-sha256>/
    ghidra-project/
    decompiled/
    index.sqlite
```

The `.local/` directory is gitignored. Tooling creates these paths on first use. Do not commit Ghidra projects, decompiled pseudocode, or the game executable.

## Public repository

Tracks scripts, configuration templates, aggregate metrics, and original architectural analysis—not reconstructed game source.
