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

## Final consolidation slice

Relative to the reviewed `5ecde67c`, this slice removes 441 net enabling
lines, leaving +1,058 relative to the R2b baseline. The test-module rename
carries zero credit. Policy data and documentation are excluded.

| Responsibility | Removed work and replacement owner |
| --- | --- |
| Agent tool responses | The replay and case projections use fixed bounded views instead of iterative stripping. Independent replay and diagnosis share one envelope. Full diagnostic reports remain on disk. |
| Metal execution | The core frame executor captures host identity, command, input/request and shader digests, parsed completion and fenced outputs. Direct replay, diagnosis and the two trusted comparisons share it. Checkpoint receipts retain their historical shape. |
| Scientific results | Current independent replay derives its status from the canonical diagnosis. The archived classifier and legacy outcome serialization remain for compatibility. |
| OpenGL and evidence | Authority attribution reuses the common verified pixel comparison. Plain file hashing and leaf-member checks share artifact helpers. Strong no-follow and scope-specific package checks remain with their existing owners. |
| Function policy | The protected case requires all 414 observations at the pinned corpus identity. The public revision qualifier exercises both successful reuse and rejection after a relevant source change. |
| Runtime ownership | The generic frame CLI delegates UV2 scope to its probe owner. The sprite command no longer exports an import alias. Two archived reproduction scripts still import the older frame and comparison names. Continuation and capture lifecycle retirement stay with R4/R6. |

The reductions leave a budget deviation. Larger remaining savings would
require further consumer migration and verification. The approval and
independent review requirements remain open.

## Residual consolidation after `08d56ed1`

| Existing responsibility removed | Canonical owner and migrated callers | Preserved behavior | Enabling LOC |
| --- | --- | --- | ---: |
| Parallel replay classifier and duplicate scientific fields in current replay reports | The canonical diagnosis drives the standalone CLI, agent projection, portable case packaging and the archived efficiency reader. Only the historical CLI outcome shape is still projected. | Unusable evidence, partial coverage, confirmed differences, fault experiments and exact equivalence keep distinct outcomes. Historical flat replay reports remain readable. | −74 |
| Separate compact divergence and cap handling | Agent case inspection and replay share their essential divergence projection. Qualification uses the same measured response and cap refusal. | Full report and receipt locators retain their original bytes. Bounded state values point to the full receipt when needed. | −11 |
| Repeated original GL image admission and signed authority file verification branches | GL qualification shares pinned BGRA reference loading across the selected candidate and historical authority paths. Historical attribution checks file identities together before revalidating evidence. | Original renderer authority, package bindings, exact pixels, default-drawable and depth refusals remain covered. | −27 |
| Separate shader member and checkpoint filename validation | Portable case verification reuses its contained manifest path and the artifact leaf-name predicate. | Capture and recipe identity, artifact digests, sequence completeness and signed receipt checks retain their existing scope. | −6 |
| Historical import aliases | Frame and packed-pixel owners serve the remaining reproduction scripts. Historical command names still delegate for documented CLI use. Generic diagnosis tests moved with zero LOC credit. | Published evidence and archived outcomes retain their identifiers. | −8 |
| Metal execution identity | The shared execution record carries the native shader compile options. Both protected comparisons bind the build recipe and environment through the verified candidate identity. | Fast-default and precise-default executions remain distinguishable. | +9 |
| **Net enabling source change** | | | **−117** |

This leaves **+941 enabling LOC** relative to R2b and a **1,141-line
deviation** from the original −200 target. Scientific renderer source is
unchanged in this slice. Test source changes by a net −52 lines. The
canonical classifier exercises the retired classifier's unique invariants,
and the qualification interface gains explicit bounded-value and locator
checks.

The signed original-API GL command remains an active agent and CLI entry
point with a reviewed external renderer protocol. Selected candidate execution
uses trusted in-process observation over its own offscreen contract. The two
execution bodies still have distinct signature, drawable, and depth
semantics. Retiring the historical execution body requires verified migration
of its active default-drawable consumers or a supported pinned reproduction
deployment. Live DSH campaign preflight still calls the mutable worktree
verifier. That path accepts uncommitted trials and publishes a diagnostic
build record with cooperative input separation. Protected revision
qualification uses the immutable fenced build. Migrate the remaining live
campaign protections and cached mutable build with R4/R6 replacement proof.

## November 14 approved retention disposition

The committed complete-manifest digest remains
`5a361b47f66113b14aab7d6d9ce3bd9014cc6c02a162f02efb4755e8938e72f2`.
The only recovered manifest hashes to
`40ca68d81410d9fae6ad3f431f999920f691586ac5c7a685a886795636874feb`.
The original bytes are unavailable in the checked archive and historical
worktree locations. Exact field differences require those bytes.

The approved raw-pair diagnostic uses tightly packed 1728×1117 BGRA8
inputs with lower-left origin. Protected policy pins both raw hashes, their
archive locators, comparison policy, region definitions and measured result.
The verified 26,042 pixels exceed channel tolerance 8. Maximum channel
error is 228. The original complete-manifest commitment is preserved with
`complete_manifest_unavailable`.

The qualifier reads and verifies the raw bytes on each current execution.
It regenerates the comparison using protected metadata and writes
`raw-pair-comparison.json` as a signed case artifact. Aggregate verification
checks that artifact and the signed decision. A new scientific execution
checks availability and integrity of the archived raw inputs. The recovered
manifest and report provide no authentication inputs to this route.

## Test discovery reconciliation

The 4,448-to-4,439 change is fully accounted for. Thirteen standalone
classifier tests became one canonical test with 15 subcases, a reduction of
12 discovered methods. The new compact value and locator check runs in
`ToolTests` and its two derived suites, adding three. The diagnosis and
pixel test module moves preserve discovery and add zero.

| Removed classifier tests | Surviving canonical checks |
| --- | --- |
| `test_invalid_checkpoint_blocks_qualified_match`, `test_complete_exact_match_is_qualified` | Invalid checkpoint provenance refuses; complete exact diagnosis exits zero. |
| `test_invalid_coverage_is_invalid`, `test_invalid_pixel_evidence_is_invalid`, `test_invalid_state_coverage_is_invalid`, `test_unknown_verdict_is_invalid` | Unknown operations, invalid or unknown pixels and invalid state cannot qualify and yield the unusable-evidence exit code. |
| `test_partial_state_coverage_is_nonqualifying`, `test_unverified_identity_can_never_qualify` | Partial state and unverified session identity remain incomplete, including when pixels differ. |
| `test_successful_fault_experiment_keeps_exit_three`, `test_fault_without_divergence_is_never_qualified`, `test_fault_with_invalid_evidence_is_invalid` | Faults with and without pixel differences exit three; invalid evidence still exits one. |
| `test_authoritative_divergence_is_not_qualified`, `test_diagnostic_only_difference_can_qualify` | Intermediate authoritative divergence blocks equivalence; diagnostic-only differences can coexist with exact qualified scope. |

## Final-gate implementation slice

Raw-pair qualification replaces the complete-package comparison in
`gfx_frame_compare`. `gfx_revision` dispatches the functional case kind.
Net enabling change is **+28**. Protected policy adds **24** net lines.
Test code adds **79** lines for actual mandatory-path scope, input and
artifact checks. The frame seal and Metal ABI test moves count zero.
Scientific renderer source is unchanged. The measured enabling total is
**+969 relative to R2b**, or **1,169 above the R3 target**. The deviation
carries forward in cumulative phase accounting. Operator review of the
phase-specific exception follows the authentic qualification.

The verified offscreen adapter stays in place. Changing the historical
package builder would change its pinned authority digest and requires
separate compatibility review. The adapter excludes the original reference
digest and its BGRA conversion before candidate execution. The legacy
replay outcome remains a projection for verified standalone and portable
case CLI consumers. Frame loading now verifies the digest of the exact
bytes it decodes. [Remaining ownership](approved-plan.md#r5--test-optimization-and-legacy-reduction)

## Development capture and native recorder

The credential-free development capture moved routine launches to the
existing lease and capture owners. It removed **900 net enabling lines**,
**616 test lines**, and **90 documentation lines** between `d7086fb2` and
`9cbdf238`. It removed token provisioning, the launch authorization ledger and
operator delegation. Host-wide exclusion, process ownership, timeouts, cleanup,
no-overwrite output and evidence integrity remain with the existing launch,
capture and evidence owners. Configured autonomous workflow budgets remain at
the campaign boundary.

The native full gate moved from 4,440 to 4,393 executed tests, with the same
69 skips. The test diff removed 52 method definitions and added five. The
removed cases exercised token validation, authorization issuance/consumption,
reservations and obsolete initial-state or bridge requirements. The surviving
launch lease, capture, campaign and bridge checks cover exclusion, cleanup,
settings restoration, output protection and configured workflow limits.

From `9cbdf238` to recorder checkpoint `627dcf1d`, the native recorder adds
114 net scientific observation lines and the existing harness adds 123 net
test lines. These changes cover submission and target evidence, failure JSON,
and correction of the resolve query shared by the recorder and interposer.
The slice changes no enabling or protected policy source. Against R2b
`f43cb4d5`, cumulative net changes at `627dcf1d` are **+69 enabling**,
**+668 scientific**, **+296 protected policy**, **+294 tests**, and
**−168 documentation** before this closure reporting. These buckets use the
existing enabling ledger and assign recorder/comparison behavior separately,
with moved source counted once. This reporting leaves documentation at −98 LOC.
The R3 enabling deviation is **269 lines above the −200 target** and remains
pending explicit review. The cumulative repository target remains in force.
