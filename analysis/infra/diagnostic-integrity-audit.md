# Diagnostic integrity audit (PR 1)

Scope: the original-OpenGL observer, Metal checkpoints, the replay harness and
the resource-capture paths that M4–M6 use. The audit is a code and artifact
review; PR 1 fixes are listed separately from open risks.

## Method

1. Read the instrumentation sources: `benchmark/eu4_gfx_frame_trace.c`,
   `eu4_gfx_checkpoint.mm`, `eu4_gfx_frame_replay.mm`, `eu4_gfx_draw_route.mm`,
   `eu4_gfx_generic_draw.mm`, `eu4_gfx_init.mm`, `eu4_gfx_shader.mm`.
2. Read the consumers: `automation/m6_frame.py`, `m6_compare.py`,
   `m6_live_accept.py`, `m6_live_capture.py`, `gfx_compare.py`.
3. Re-ran the existing native regressions for the historical failures (GL state,
   pre-image ordering, resolve/viewport/sampler mapping).
4. Replayed the feasibility-spike artifacts through the new health scanner
   (`.local/infra/spike/run-5/...`): a rejected `_GfxDraw` is detected from
   `draw-refused-edfcd1.json` and `stop.json`.

## Verified properties

| Property | Evidence |
| --- | --- |
| GL readback preserves active texture unit and that unit's bindings | `tests/gfx_trace_state_host.m`; `tests/test_m6_frame.py::TraceStateHostTests` |
| Texture readback no longer corrupts texture unit 0 | `read_texture` operates on the current unit only; `menu-15` invalidated as a pixel reference |
| Pack state is restored (alignment, row length, skip rows/pixels, pack buffer) | PR 1 fix + extended native host regression |
| Renderbuffer binding is restored by target observation | PR 1 fix in `capture_target`/`capture_target_pixels` |
| Readbacks are synchronized with completed GPU work | `glFinish` + `glReadPixels`; `waitUntilCompleted` at checkpoint/replay readbacks |
| Isolated replay initializes attachments/depth, then uploads the pre-image, then encodes the draw | `eu4_gfx_frame_replay.mm` ordering; `test_pre_image_survives_the_selected_draw` fails under the old order |
| Resolve/copy orientation is converted for the GL texture convention | `resolve_scaled` V flip; native four-quadrant regression |
| Sampler `texN` maps to unit N (including a missing `tex6`) | translator regression; live map changed from beige to green |
| Offscreen viewport uses the target extent, not the backbuffer HiDPI ratio | `eu4_gfx_draw_route_logical_extent` regression |
| Capture provenance carries executable/library SHA and session identity | `gfx-checkpoint/v1` fields; probe report provenance |

## Findings and disposition

### Fixed in PR 1

1. **Texture readback did not restore the full pack state.** It restored
   `PACK_ALIGNMENT` and `PACK_ROW_LENGTH` but not `PACK_SKIP_ROWS`,
   `PACK_SKIP_PIXELS`, or `PIXEL_PACK_BUFFER_BINDING`. A PBO bound at draw time
   would have received the readback instead of client memory. Fixed in
   `benchmark/eu4_gfx_frame_trace.c` (`capture_texture_begin/end`) and covered by
   the extended `gfx_trace_state_host.m`.
2. **Target observation left a renderbuffer bound.** Both target descriptors and
   target pixel reads now save/restore `GL_RENDERBUFFER_BINDING`.
3. **Window capture silently disabled qualification.** `m6_live_capture` never
   passed the drawable extent, so `_window_capture` returned
   `status: captured` with `qualified: false` and exited 0. It now forwards the
   checkpoint extent and treats an unqualified window as a failed diagnostic
   result. Regression: `tests/test_m6_live_capture.py`.
4. **No capture-validity vocabulary.** A file existing on disk was implicitly a
   success. `automation/gfx_diagnostics.py` now returns `valid`, `invalid`, or
   `unverified` for checkpoints, probes, replay results and frame observations;
   missing/contradictory fields can never be `valid`.

### Open risks (documented, not silently accepted)

1. **Sampler units ≥ 16 are dropped** by the trace reader without a diagnostic
   (`FTRACE_MAX_UNITS`). A trace could claim fewer textures than the draw used.
   The PR 4 semantic comparison must report `trace_truncated` rather than infer
   equality; a native warning is still desirable.
2. **`eu4_gfx_gl_interpose.c` is not in a build list.** `copy-tex-sub.jsonl`
   should be treated as dormant evidence until the file is compiled into an
   observer.
3. **The after-present checkpoint failure paths** in `eu4_gfx_checkpoint.mm`
   pass caller RVA 0 and do not honour the checkpoint stop flag. The new health
   gate classifies a failed readback by its artifact, but the native path should
   be corrected when it is next touched.
4. **Pixel format and row stride are not validated by `m6_compare`.**
   `gfx_compare` validates them; the new PR 3 comparator will require them and
   `m6_compare` is left unchanged for backward compatibility.
5. **`m6_compare.write_difference` ignores the row-origin normalization** it may
   have applied to the candidate. The difference image is therefore wrong for a
   cross-origin pair. PR 3's difference writer applies normalization uniformly;
   the old path is documented as diagnostic-only.
6. **`m6_live_accept` accepts only `probe.overall == "captured"`,** rejecting the
   stronger `"presented"` outcome. This is a policy bug in an M6 checker; the new
   harness reports statuses separately and does not reuse that gate.

## Health gate semantics

* A rejected draw, pipeline/shader creation failure, failed command buffer,
  failed resolve/copy, Metal validation error, or failed readback fails the
  current diagnostic result immediately.
* Repeated validation messages are aggregated by a normalized signature
  (addresses, counts and quoted payloads collapsed); the first actionable
  failure retains its source artifact and detail.
* A shader-cache or other configuration failure is classified as
  **instrumentation**, not as a renderer defect.
* Command-buffer completion is **not** pixel correctness. `pixel_correctness`
  stays `null` unless an aligned comparison produced it, and a pixel-requiring
  request is gated to `unverified` without one.

## Regressions added

- `tests/test_gfx_diagnostics.py` — capture validity, rejected draw, aggregated
  validation, instrumentation distinction, completion ≠ correctness.
- `tests/test_gfx_wait.py` — progress-aware deadlines, saw-tooth stall detection,
  process-exit terminal, no-progress deadline.
- `tests/test_gfx_session.py` — owned-only termination, PID-reuse identity,
  unrelated process refusal, concurrent session registry, atomic dual claims.
- `tests/test_gfx_artifacts.py` — content-addressed dedup, preserved-evidence
  protection, bounded eviction, explicit drops.
- `tests/test_m6_live_capture.py` — drawable extent forwarded, unqualified
  capture is not success.
- `tests/test_m6_frame.py::TraceStateHostTests` — extended pack-state/PBO
  restoration.
