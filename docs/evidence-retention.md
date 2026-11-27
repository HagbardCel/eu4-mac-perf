# Evidence retention and restore

Restore historical evidence from read-only sources. Keep the original identity and verifier scope. [Harness commands](harness.md) · [Current capabilities](status.md)

## What is retained

Keep sealed cases, negative controls, first divergent checkpoints, producer and shader receipts, and campaign claims and settlements. Retain original streams and verify the decompressed digest of compressed traces. Retire derived copies after proving an isolated restore. Public records carry paths, sizes and digests. Keep raw game, shader and texture bytes and private transcripts in authorized storage.

## Discovery

```sh
export EU4_EVIDENCE_ROOT="<root1>:<root2>"
python3.14 -m automation.agent_tools eu4_session discover --query <text>
python3.14 -m automation.agent_tools eu4_session discover --limit 20 \
  --offset 20 --locator-limit 20 --locator-offset 0
```

Discovery is read-only and bounded to configured roots. Locator counts distinguish scanned, filtered and returned rows. A truncated verified lookup returns `truncated_unverified` and cannot be used for recovery.

## Inventory

```sh
python3.14 -m automation.case_locator inventory --root <root> --offset 0 --limit 20
python3.14 -m automation.case_locator inventory --root <root> --verify
```

`publish` copies regular files, rejects source and staged symlinks, compares the staged and original inventories, and re-verifies any existing publication before adoption.

## Restore trial

1. Record the exact manifest or locator identity, verification state, relative path and size.
2. Materialize regular files in fresh scratch with an explicit evidence root. Refuse symlinks and paths that escape the restore. Use the unchanged trusted descriptor or portable case format for relocated frames. Original absolute paths are provenance. A missing member must fail while its producer copy remains available.
3. Recompute exact equality for every applicable attachment channel under the original observation contract. RGB8 compares RGB, RGBA8 compares RGBA, and depth/stencil use their separately declared format and scope. Diagnostic planes remain diagnostic.
4. Report missing members and expected digests with the configured discovery roots. Keep restores private and measure allocated space around authorized archival. Retain the source until all pinned consumers, including parallel worktrees, work from the retained copy.

## Restore verification

A byte-identical restore matches every `case.json` member size/digest and the locator's whole-tree size and file count. Verification refuses missing bytes, missing digests, byte-size mismatches and digest mismatches. `python3.14 -m automation.test_runner run --profile offline --module tests.test_case_locator` exercises publication, producer removal and restore with generated fixtures.

## Protected collections and recovery status

`R0` means the audit-host root `.local/r0/6710a341d864466d7b0ab2d297ab513922d582b6`, and `R1b` means `.local/r1b-evidence` on that host. **Producer hold** prohibits retiring the named directory and its bound inputs until an independent, verified copy and consumer checks exist. A scratch copy from the producer establishes recoverable bytes on that host. **Independent** means the bytes were restored from a retained copy separate from the producer. Both differ from fresh execution, which binds a new candidate, renderer, shader and recipe receipt. [Published verification records](../plans/repository-refactor/r1b-verification.json) · [Original R0 inventory](../plans/repository-refactor/r0-evidence.md#protected-evidence)

| Collection | Retained source, verifier and scope |
| --- | --- |
| M1 coexistence, M2 strategy and profiler, Phase C | Recover the M1 probe receipt and flush log and the M2 findings from Git, with their original platform and workload limits. Preserve any private held-out/profiler inputs at the producer roots named in their findings until separately restored. Phase C is the tracked capsule at `results/20261003T210202Z-intrusive-diagnostic`; `eu4_frame_model verify-evidence --profile intrusive_complete_v1` verified all 14 declared files and semantic power evidence. [M1/M2 dispositions](../plans/repository-refactor/r0-evidence.md#protected-evidence) · [Phase C capsule](../results/20261003T210202Z-intrusive-diagnostic/raw_evidence.json) |
| M3 Simple draw, `0xedfcd1` | **Producer hold** on `.local/gfx-experiments/simple-02/draw-03`, `simple-03/clear-02` and `simple-04/after-draw-01` under the same root. Check the final BGRA, JSON and PNG against the three recorded digests. These matched in scratch. The CPU sample and GPU completion retain their M3 scope. [Original runs and digests](../analysis/reconstruction/simple-loading-draw/live-evidence.md) |
| M4 `gl-reference-07` | **Producer hold** on manifest paths under `.local/m4/experiments/`. In scratch, check 32 entries and the campaign manifest pin, load the recorded GL and Metal logical captures, and call `gfx_compare.compare` with the original policy. Zero of 1,930,176 pixels differed. `campaigns/eu4-m4/accept.py:presentation_gates` separately passed checkpoint, drawable and window checks. [Pinned manifest](../analysis/m4/evidence-manifest.json) · [Original qualification](../analysis/m4/qualification-report.md) |
| M5 `sprite-gl-ref-09` | **Producer hold** on manifest paths under `.local/m5/experiments/`, including `metal-live-final-02`. In scratch, verify both manifests, 12 declared artifacts and the GL vertex sidecar, then run `gfx_compare.compare` and `campaigns/eu4-m5/accept.py:live_engine_gates`. Seeded comparison returned zero of 1,930,176 pixels. Live reach and composition passed while live pixel qualification remained false. [Pinned manifest](../analysis/m5/evidence-manifest.json) · [Live evidence](../analysis/m5/live-evidence.json) |
| M6 `menu-occ300`, present 24360 | **Producer hold** on `.local/m6/frames/menu-occ300/{frame,replay-final}` and `.local/m6/live-start{3,4,5}/run-1`. In scratch, `gfx_frame.load_trace(frame, verify_blobs=True)` verified 1,125 blobs and the historical stream/reference digests. `gfx_frame_pixels.compare` verified the recorded 326-event captured-state output with zero pixel differences including alpha. `m6_live_accept.check_run(..., require_resolve_trace=True)` separately accepted all three digest-bound settled starts. **Independent** orientation PNG at `R1b/m6/menu-occ300/frame-reference.png`. Its explicit regression ran with all four inputs. [Historical manifest](../analysis/m6/menu-frame-evidence.json) · [Live receipts](../analysis/m6/live-acceptance.json) · [PNG recovery](../analysis/m6/qualification-report.md#orientation-fixtures-and-historical-images) |
| M7 present 615, `2564021f-4e00-4a58-a448-043697249a91` | No retained copy or authorized archive was found in the M7 producer, campaign and R0 roots. Recover the original seal, stream, blobs, GL reference and bound shadow outputs by the published manifest/stream/reference digests, then use `gfx_frame.load_seal` and the recorded shadow comparator. Hold the collection from replay, retirement and any new qualification claim. [Readiness commitments](../analysis/m7/m7-readiness.md) |
| M7 presents 616 and 904 | **Producer hold** on `.local/m7/goal-round1/{capture,capture-c}`, including original shadow outputs, and the retained 904 A/B inputs/outputs named in the report. Verified both frames from producer scratch with `gfx_prefix_check.prepare_trusted_evidence`, `verify_trusted_evidence` and `gfx_frame.load_seal`. The verification record gives each seal and trace-manifest digest. Keep separate checkpoint scopes. [616/904 audit](../analysis/harness-remediation/audit-20261120.md) · [904 A/B procedure](../analysis/harness-remediation/compressed-904.md) |
| M7 GL control `90825686…` | **Producer hold** on `.local/m7/pr4/{capture-20261121c,control-out-b,diag-*}` and the bound authority files. The same scratch seal verification passed and the control BGRA matched its historical signed receipt digest. The 347-pixel comparison remains diagnostic and the current `gfx_gl_control_qualification` gate refuses at `drawable_equivalence=unproven`. [Seals, request and authority commitments](../analysis/m7/gl-control-qualification-90825686.md) |
| M7 trusted prefix `47590918…` | **Independent** frame and descriptor at `R0/restored-reference` (1,192 files, root SHA-256 `b3e3d7b5d1e4cefe814f460c99a19c32011242d100a8b0c5bdd97aa9e485756c`) and bound replay/request/shaders at `R1b/m7/47590918-bound-replay`. Copy both to scratch. Run `gfx_prefix_check.evaluate(frame, metal, request_path=request, require_end=20, trusted_root=root)`. Reproduced R0 prefix `[0, 0]` and event 1 on RGB8 `tex:433`, 23 RGB pixels / max 1. Full R1b outputs at `R1b/m7-bound-recheck.json` and `R1b/m7-independent-recheck.json`. [Scope and root binding](../plans/repository-refactor/r0-audit.md#reproduced-scope) |
| M7 Strategy B `47590918…` | **Independent** publication at `R0/private-evidence`. Use `case_locator locate --root <restored-root> --key 47590918-d63a-4798-9737-df00285ad51b-1-e3ab1d62cbbc86dc`. Case-manifest SHA-256 `560f412259c9961e0bb823feb3ad86f1e0c162eee8a073c0c6ac571bed5f181c`. `safe_to_reuse=true`; 1,317 verified files and `graph_status=invalid`. The package remains diagnostic. [R0 publication and restore](../plans/repository-refactor/r0-audit.md#reproduced-scope) |
| M7 historical interventions and separate sparse seals | **Producer hold** on `.local/dsh/m7-event3/workspace` for the historical candidate, receipts and matched-null records, and `.local/m7/event1-fragments-20261124T195523Z` and `event1-fragments-20261124T220739Z-63934` for sparse seals. Recheck historical `exp-canonical-check`, `exp-fastdiv` and `exp-div-rcp` from their producer receipts and output hashes (28 entries each verified). The 20-pixel candidate remains the original matched-null input. Its v2 recipe lacks native compile-mode attestation. `gfx_frame.load_seal` verified sparse seals `482a6500…` and `1efc8880…` from producer scratch. The later 24-bit control refusal and zero UV2 pixels leave correspondence unresolved. [Candidate, shader and recipe identities](../plans/repository-refactor/r0-evidence.md#event-1-numerical-identity) · [Interventions and sparse evidence](../analysis/m7/event1-investigation.md) |
| Function oracle `gfx-live-20261025T155406Z` | Operator archive `pr-d-gfx-live-20261025T155406Z` is unavailable. The Git publication manifest matches the E1 certificate. The canonical and configured roots yielded zero original members. Recover the 64 `captures/*.capture` files plus `metadata.json`, `session.done`, `startup.status` and `summary.native`. Verify with `oracle/recorder`, `oracle/function` and `oracle/certify` before original-session replay or retirement. The separate 13-case pilot and 414-case emulated corpus remain Git-recoverable with their own target/code-window checks. [Archive and manifest digest](../results/20261025T155406Z-gfx-live-publication/publication-summary.md) · [Pilot](../analysis/reconstruction/_GfxSetConstantBuffer/pilot/pilot.md) · [P-code verifier](../analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md) |
| L3.2b packets | **Producer hold** on `.local/automation/eu4-l32b-effective-state/captures/l32b-i-effective-state` and the v2 coordinator root `.local/automation/eu4-l32b-complete` in its producer worktree, plus the private packet/checkpoint roots named in its receipts. Preserve original shader and texture members by their receipt digests. Four v1 packet files, the review pin and v1 campaign manifest verified. V2 completion verified from Git publication `b633b9ad` with its pinned ledger and the contract manifest verified separately. Keep the v1 checker frozen, `packet_ready=false`, and v2 completion distinct from the separate qualified review. [V1 evidence and capture pin](../analysis/gfx-draw/v0/l32b/effective-state/note.md) · [V2 completion](../campaigns/eu4-l32b-complete/l32b-manifest.json) · [Contract pin](../campaigns/eu4-l32b-contract/contract-manifest.json) |

### R2b OpenGL authority

**Producer-held.** On the native review Mac, retain the producer paths below. The project checkout also holds a byte-verified copy of 25 files (157,628 bytes) at `.local/r2b-authority/07453ebc`, called `H` below. Independent restoration has not been established. Preserve the original reviewed executable because its digest depends on the specific compiler and SDK. Preserve receipt paths for verification. [Independent review and production verification](https://github.com/HagbardCel/eu4-mac-perf/pull/304)

| Evidence | Producer path, retained copy and identity |
| --- | --- |
| Independently reviewed executable and generated driver | `/private/tmp/eu4-r2b/.local/r2b/authority-a/{eu4_gfx_draw_control,gfx_gl_control_renderer.py}`, copied to `H/reviewed/`. Native SHA-256 `092a3cdb9075974116fbf8458e7375b83e04d619872a23f1323744a3509cb617` and driver `153fd9647236bde7eb1b60d91074fa0f3370d22782d62c5e88718f79d6cb3feb`. Production used the matching pair at `/private/tmp/eu4-r2b/.local/native-builds/gl-control/153fd9647236bde7eb1b60d91074fa0f3370d22782d62c5e88718f79d6cb3feb/`. The older registered executable at `/private/tmp/eu4-r2-evidence/gl-base/eu4_gfx_draw_control` is also retained at `H/historical/eu4_gfx_draw_control`, SHA-256 `254618ef8ab232fd1b3c42e6186d642702e714e70d79c92f6d2d908cfa3abc7a`. |
| Build recipe and native identity | The reviewed `python3.14 -m automation.native_build gl-control` build used Apple clang `21.0.0 (clang-2100.4.1.1)`, Xcode `27.2 (27B52)`, macOS SDK `27.2 (26B118)` and linker `27106.1`. The deployer checks the compiler, SDK build and artifact digests. The recipe targets `arm64-apple-macos27.0` with `-isysroot <SDK> -O2 -g0 -w -framework OpenGL`. Native source SHA-256 `9cbb09f45f4e455887e810c4827e50baec93869093a04cb08e7b4467255ed1ae`. Verify ARM64, `LC_UUID` `4EF9AEEA-B000-3E3A-B359-D531445CF88A` and Mach-O load-command SHA-256 `0aec2c5d6b87c1abaf93b129f56e51b8e67aadc0328f0076e3b6384f941f4cba`. [Pinned deployment recipe](../automation/native_build.py) |
| Independent native review, synthetic package and reference | `/private/tmp/eu4-r2b-native-review.c0SkIIUx/output-unsandboxed/`, selected files copied to `H/review/`. `review.json` records **APPROVE**, SHA-256 `37138545a6febc13dbb168c6fe083f556781bc2c249c3eb58b327e9ffaac4f35`. Retain `package/`, `expected.bgra`, `test-public.der` and `run/{request.json,receipt.json,result.bgra}`. Package SHA-256 `704fce9fd9ccf031cd3761ca3ae50a1ab1f59ed7c3d1c79a69c0194e7cc11513`, reference and result `e5f58358cc2305886bae06af04a2c55098b36d97e2e9eb01dbd9e4cef885b0a7`, test-key receipt `e8816ecb0aaf6ee1c5c2ea5fe89ec914f39ec30bf62ecb34032e9c5973283db0`. |
| Registered production verification | `/private/tmp/eu4-r2b/.local/r2b/production-gl-io_norei/`, selected files copied to `H/production/`. Retain `package/`, `verification.json` and `out/renderer-run/{request.json,receipt.json,result.bgra}`. Request SHA-256 `09ff858b1b2bf2d73070dbc3ba110657ee5d839c7b484f02e056896a008c3150`, signed receipt `bcb490e9faffb771ccfd731d6dcd00829ee5382839a55f7b1bdd802b3f690c58`, verification record `d13de6dab72b433f5ed7aa8b1e498216f8db443fcf92c88b0cc00be26ad21394`. The package and 16-pixel BGRA match the review digests above. |

Use SHA-256 for artifact identity, `gfx_gl_replay_input.verify_package` for the restricted packages, and `gfx_gl_control_qualification._validate_receipt` for the original request, recorded component paths and public-key binding. The input verifier SHA-256 is `6c93974f4df9612c45c0645ad27a0d6cd5b3a7885ea2b6829be43b5401c02166` and receipt signer SHA-256 is `52e03dd0c0b80c10c4e8acf2f72d4307a07c2c0321a06bd1107e0903f3ffa3b4`. OpenSSL verified both copied receipts with their respective public keys and canonical JSON signing bodies. This collection establishes synthetic registered default-framebuffer execution. Authentic drawable equivalence remains unresolved. [Authority registry](../automation/gfx_gl_control_authorities.py) · [Package verifier](../automation/gfx_gl_replay_input.py) · [Receipt verifier](../automation/gfx_gl_control_qualification.py)

### R3 authentic OpenGL selected draw

The operator deployment pins original locator
`74c3f427-1fed-4545-bc26-0d968cd07f5d-1-31f9fe67f1558872-reference`, published
package manifest SHA-256
`2c8af5db28b51900628e7030c965041f8b152d8c3e64c788dd597823a45a8c99`, and original
seal SHA-256 `31f9fe67f1558872719904bd14a0b7ec4df74c0839d131dae0befc79b6c62927`.
The package contains 1,165 verified frame members plus its manifest. The
seal binds the original frame manifest and event stream. The direct pre-draw
image binds SHA-256 `e139eeac793146caccdfa4f6735bc1d16e81a96c57916084e501ffec5acd1c2a`
and the original post-draw image binds SHA-256
`63c6d8c04571f84843ead7f8326b441ce85dcbc98ca9d6625003fdfdb593aad0`.

The private retention root is `.local/r3-original-evidence/` in the primary
checkout. The maintained locator and sealed-reference verifier recovered the
eligible event 273 reference using only that root, independently of the
producer worktree. `automation.case_locator locate`,
`gfx_prefix_check.verify_trusted_evidence` and `gfx_frame.load_seal` check
publication identity, payload inventory and original seal. The incomplete
captures and debugger observation stay in their separate diagnostic runs.

Retained qualification records and their artifact trees live under
`qualification/` in the same private root. `qualification/final.json` locates
the final immutable signed aggregate and authentic case, both worker receipts,
the protected deployment snapshot and the signed artifact inventories. Recover
signed case and output paths at their recorded run locations for
`dsh_receipts.verify_revision`. Private signing material remains in the
operator deployment. [Original observation and scope](../plans/repository-refactor/r3-capture-preparation.md#authenticated-selected-draw-qualification) ·
[Aggregate verifier](../automation/dsh_receipts.py)

## Gate R1 retrieval acceptance

Start at the [renderer index](renderer/README.md). These four paths resolved to the named evidence, verifier and recorded recovery status.

| Index path | Identity, verifier, recovery and limit |
| --- | --- |
| Findings → M4 Simple draw → evidence | `gl-reference-07`, raw SHA-256 `69d41a8870679dcb3158bd3f3fad2f4afff11106e45990ab9a3ffc0f5dcc7670`. Producer-held, verified in scratch by `gfx_compare.compare`. Exact logical output under the fixed policy. Window checks are separate. [M4 qualification](../analysis/m4/qualification-report.md) |
| Findings → M7 axial displacement → matched-null experiment | Seal `47590918…`, historical 20-pixel candidate and recipe `533ca57d…`. Producer-held matched-null JSON and receipt. Verify the declared receipt hashes and compare the four one-pixel axial directions to the gradient-range-matched null. All four fell below it. The result is scoped to those directions and that candidate. [Experiment](../analysis/m7/event1-investigation.md#experiments) · [Numerical identities](../plans/repository-refactor/r0-evidence.md#event-1-numerical-identity) |
| Findings → M7 sparse observation → sparse investigation | Seal `1efc8880…` on framebuffer 6. Producer-held, verified in scratch by `gfx_frame.load_seal`. The original sparse probe measured 32-bit normalized depth, refused its 24-bit control and returned zero UV2 pixels. Fragment correspondence remains unresolved. [Sparse investigation](../analysis/m7/event1-investigation.md#one-use-capture-after-review) |
| Functions → `_GfxSetConstantBuffer` → archive identity | `gfx-live-20261025T155406Z`, operator archive `pr-d-gfx-live-20261025T155406Z`. Git manifest matches the E1 certificate. The 64 original transcripts are unavailable. `oracle/recorder`, `oracle/function` and `oracle/certify` are the recovery verifiers. Replay from original transcripts stays blocked. [Publication](../results/20261025T155406Z-gfx-live-publication/publication-summary.md) |

## Verification and budget

The [published runner records](../plans/repository-refactor/r1b-verification.json) preserve the 137-test focused run, the named M6 orientation regression (1 executed, zero skipped), and all six exact native failures on both arms. The runner comparison found zero new failures. The hosted base ran 4,396 tests (3,915 executed, 481 skipped) and the initial PR ran 4,395 (3,914 executed, 481 skipped). Only the historical audit test left discovery. The local Linux arm64 run was 4,394 (3,900 executed, 494 skipped). Its environment differs from hosted x86-64, and the suite has architecture- and tool-dependent test prerequisites. The hosted pair provides the controlled count comparison. [Hosted base](https://github.com/HagbardCel/eu4-mac-perf/actions/runs/45712611168) · [Initial PR workflow](https://github.com/HagbardCel/eu4-mac-perf/actions/runs/45775183807)

Against `bb2eeaf8`, the [R0 physical-line counting rules](../plans/repository-refactor/r0-audit.md#source-baseline) give **116 authored additions, 153 removals, net −37**. The generated block within the audit map is counted separately. Retired generated files and block reduced CSV by 273,626 bytes, JSON by 1,340 bytes and Markdown by 1,882 bytes (84 lines). The published verification JSON adds 15,436 evidence bytes. The three retired PNGs reduced binary payloads by 23,516,652 bytes.

Lifecycle labels and ignore rules alone cannot authorize retirement. Use a run or content identity. Prove compressed and expanded copies decompress to identical bytes. Preserve the original after a failed restore.
