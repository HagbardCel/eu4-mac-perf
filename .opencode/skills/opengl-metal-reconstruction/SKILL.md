---
name: opengl-metal-reconstruction
description: >
  Use when reconstructing EU4 OpenGL renderer functions for
  Metal. Verify original instructions, Metal API contracts,
  semantic mismatches, and boundary tests before implementation.
---

# OpenGL-to-Metal reconstruction

A missing `.local/metal-docs` directory does not block this skill.

Before modifying implementation code:

1. Inspect the original instructions and the callers that show how the values are used.
2. Search `analysis/metal-api/index.md` and read the contract it names.
3. Read the installed SDK headers for the exact signature. Locate them with `xcrun --sdk macosx --show-sdk-path`. The corpus records established constraints. The header is the declaration.
4. Keep each material claim labeled `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`. A declaration does not prove a precondition the header does not state.
5. Name, in the research note, the contract files and headers actually read.
6. State mismatches in unsupported values, coordinates, resource lifetime, and state that does not carry across a draw or a pass.
7. Derive boundary tests from those mismatches.
8. Say whether a Metal effect is applied in this replacement or left for a later encoder.

If a material constraint is still unresolved and a faithful replacement would have to guess, stop and report it with the existing `BLOCKER:` line.

An official Apple page may be opened when the network is available. A failed fetch stays `Unresolved`. Do not copy Apple's pages or headers into the repository.

This skill and `analysis/metal-api/` are present only in a checkout whose commit contains them. Where an input is valid for the original call and not directly valid for Metal, test that input. Otherwise derive the test from lifetime, synchronization, or state order. Do not invent an unsupported value just to have one.

## Route routine research through the native tools

Prefer the mounted DSH tools over composing equivalent shell commands:

- original function semantics → `eu4_re_query` (fast indexed symbols,
  relationships, ABI, layouts) or `eu4_research` (sealed instructions, detailed
  decompilation, paginated static cross-references);
- captured event resource graph → `eu4_capture` with `op=graph`;
- existing differential evidence → `eu4_diff_inspect`;
- previous session evidence → `eu4_session`;
- replay/case inspection → `eu4_replay` / `eu4_case_inspect`.

Use shell commands for work the native tools do not cover: editing source,
compiling, focused tests and specialized analysis. Do not wrap routine shell
commands in a new generic DSH tool.
