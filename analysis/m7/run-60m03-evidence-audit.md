# run-60m-03 evidence audit (M7 event-3 exact-prefix campaign)

Date: 23 November 2026 (Europe/Berlin). Session: `run-60m-03`, campaign
`m7-event3-20261122` under the shared objective
[eu4-m7-event20](../../campaigns/eu4-m7-event20/objective.md). Candidate
worktree: `.local/dsh/m7-event3/workspace` (baseline `9d92a0c0`).

This record keeps the findings produced by the full session audit. It adds no
experiment and treats the previous agent's hypotheses as hypotheses, not as
established causes.

## Established results

| Field | Value |
| --- | --- |
| Authenticated reference | seal `47590918` |
| Campaign | `run-60m-03` |
| Exact verified prefix | `[0, 0]` |
| Earliest divergence | event 1, `tex:433` |
| Canonical Metal candidate | still fails exact equality at event 1 |

The reciprocal-lowering experiment changed one vertex expression,
`(pos.x + 0.5) / 5632.0`, and reduced the diagnostic mismatch counts from
20 / 120 / 236 to 18 / 99 / 193 at events 1 / 2 / 3. At event 1 it fixed three
pixels and introduced one new mismatch. The experiment is **diagnostic**: it is
not a qualified equivalence, and it does not prove that the original compiler
used reciprocal multiplication.

## Depth diagnostic and its correction

The first depth readback reported a half-width attachment. This was a
readback-layout interpretation error: the blit of
`MTLPixelFormatDepth32Float_Stencil8` writes the depth plane packed at 4 bytes
per pixel at the start of each destination row, and the first decode read it at
an 8-byte stride. Decoding the already captured raw data at its real layout
produced a complete depth observation. The corrected values bounded the
interpolated-depth difference at about one 2^-24 quantum, which is the same size
as the colour effect; they did not establish identical UV interpolation.

The temporary native readback (`capture_depth_to_file`, opt-in
`EU4_FRAME_REPLAY_DUMP_DEPTH`) was removed rather than promoted to permanent
infrastructure. The raw depth planes remain under `.local/`.

## Trusted-validation audit

Six trusted validations were recorded between CET 00:15:03 and 00:40:34,
totalling 236.998 s.

| CET | Candidate digest | Classification |
| --- | --- | --- |
| 00:15:03 | `799e612d` | Necessary initial baseline |
| 00:25:11 | `3924f31a` | Redundant: documentation only |
| 00:26:30 | `1042a54a` | Redundant: documentation only |
| 00:33:46 | `dd09494a` | Redundant: depth experiment exactly rolled back |
| 00:38:17 | `9d4a9376` | Diagnostic-only source change; not an attempted correction |
| 00:40:34 | `c9b101b0` | Redundant: documentation only |

Four definitely redundant validations cost 158.203 s (66.8 % of validation
time). Counting the diagnostic-only validation, potentially avoidable time was
196.625 s (83.0 %). The cause was unnecessary model-initiated revalidation, not
a checker defect: the candidate digest covers every modified permitted file, so
documentation and exact rollbacks changed the digest without changing renderer
behaviour. The shared objective now states that such changes must reuse the
previous diagnostic result instead of invoking `eu4_validate_candidate`.

## Record limits

* No native EU4 research or replay tool calls were made in the recorded
  session; the work was documentation and offline diagnostics.
* The final `blocked` decision was not established by a conclusive causal
  attribution. Candidate-side inputs were measured, matched or bounded, but the
  owner of the remaining residual was not demonstrated.
* No artifact was copied into this record. Preserved local evidence:
  `.local/dsh/m7-event3/workspace/.local/m7/event3/exp-div-rcp/`,
  `.local/dsh/m7-event3/workspace/.local/m7/event3/exp-depth/`,
  `.local/dsh/m7-event3/workspace/.local/m7/event3/exp-depth2/`, and the
  candidate's `analysis/m7/reports/event3-prefix-diagnosis.md`.
