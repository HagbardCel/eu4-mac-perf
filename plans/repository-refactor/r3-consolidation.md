# Permanent qualification ownership

The review base was `453b83c8cd4eedf170a56bd05164688b2e7f33de`.

| Responsibility | Maintained owner and migrated callers | Preserved invariant |
| --- | --- | --- |
| Metal frame execution | `automation.gfx_frame.execute_frame` owns native invocation, the GPU lease, deadlines, optional requests and the fenced output inventory. The replay CLI, independent diagnostic replay and trusted prefix checker call it. Final-frame qualification consumes the same restricted execution as checkpoint qualification. The standalone final-frame worker body was removed. | Final-image and prefix assertions remain distinct. The legacy shader and producer receipt formats remain readable. |
| OpenGL qualification | The selected draw and signed original-API authority share package identity verification, evidence digests and exact packed pixel comparison. The authority route continues to verify its signature, reviewed executable and scientific attribution. | Exact state, attachment identity, shader and ordered-transition checks still apply to the authentic selected draw. Authority signatures retain their existing payload format. |
| Pixel comparison | `automation.gfx_frame_pixels` owns packed RGBA/BGRA conversion, row origin, amplified differences, PNG writing and four-channel exactness. Logical, frame, OpenGL and independent comparison migrate to it. The independent raw fallback was removed. | Raw comparisons are explicit. Invalid replay metadata refuses qualification. Regional, mirror, temporal and structural diagnostics retain their own policies. |
| Isolated sprite replay | `automation.gfx_sprite_replay` and `tests/gfx_sprite_replay_host.mm` share a native compiler recipe and fenced run with the historical sprite acceptance wrapper. The working-tree build checks content and executable identity. The permanent case stages the pinned inputs from protected configuration. | Production sprite encoder and frozen exact BGRA scope remain in use. |
| Identity and receipts | Candidate snapshots and native builds retain functional ownership. The trusted prefix checker uses the restricted Git helper. The receipt owner verifies the signed case projection, tiers, coverage and protected decision, and inventories worker artifacts with the same path checks as sealed artifacts. | Function reuse keeps the original execution revision and checks the exact retained executable, source closure, toolchain, reference and observation bytes. Verification can attest an incomplete decision. |

The maintained modules use functional names. The old frame, pixel and sprite
CLI names are thin historical entries. Protected case IDs, stored receipt
schemas, retrospective reports and reproduction wrappers keep their existing
identities. Active frame, comparator and sprite consumers use the functional
modules. Historical capture schemas and the continuous capture and session
coordinators keep their existing formats. R4 and later phases own migration
of the lifecycle, continuation, and Git checkpoint entry points.

## Measured consolidation

The first consolidation commit `b5e00135` removed a net 122 enabling
lines relative to the review base. The table counts the moved exactness
helper once and excludes pure source moves.

| Ownership | Added | Removed | Net |
| --- | ---: | ---: | ---: |
| Metal frame execution and independent replay | 158 | 225 | −67 |
| OpenGL package and authority verification | 66 | 138 | −72 |
| Packed pixel comparison | 85 | 118 | −33 |
| Isolated sprite replay | 62 | 78 | −16 |
| Policy, function reuse and receipt verification | 117 | 81 | +36 |
| Caller migration and historical adapters | 104 | 74 | +30 |
| **Enabling code** | **592** | **714** | **−122** |

The protected policy data grew by 94 lines. The native original observer
added 11 lines for executable and macOS build identity. The test migration
and reuse coverage added 22 net lines. The earlier R2b baseline and the −200
target remain recorded in the R0 disposition.

## November 14 retention disposition for operator review

The committed complete-manifest digest remains
`5a361b47f66113b14aab7d6d9ce3bd9014cc6c02a162f02efb4755e8938e72f2`.
The only recovered manifest hashes to
`40ca68d81410d9fae6ad3f431f999920f691586ac5c7a685a886795636874feb`.
The original bytes are unavailable in the checked archive and historical
worktree locations. Exact field differences require those bytes.

Both retained raw images match the committed identities. Recomparison
reproduced the recorded regions and statistics, including 26,042 differing
pixels and maximum channel error 228. The complete case remains
`invalid_provenance` in mandatory qualification. The proposed disposition
would explicitly admit a separately scoped raw-pair diagnostic by pinning
both raw artifacts, their format/extent and comparison configuration, while
retaining `complete_manifest_unavailable` and the original commitment.
Operator approval is required before adopting that disposition.
