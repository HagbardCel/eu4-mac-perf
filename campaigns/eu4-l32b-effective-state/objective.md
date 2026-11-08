# L3.2b-I — Effective state of the authentic Simple draw

You are reconstructing the effective input state consumed by the original EU4
loading-screen draw. The L3.2a probe already proved the target: `_GfxDraw` at
caller RVA `0xedfcd1`, `GL_TRIANGLE_STRIP` (mode 5), four effective vertices,
non-null shader, primitive enum 4.

Work only in this workspace. The authoritative L3.2a evidence is frozen:
`analysis/gfx-draw/v0/probe/` and `analysis/gfx-draw/v0/probe/draw-packet.json`.
Never modify, rewrite, or synthesize it.

## Required outcome

The trusted checker passes only when a fresh authentic capture from this
candidate observer establishes:

1. `vertex_buffer_bindings` — the selected vertex-buffer capture-local identity
   for each bound slot, plus stride, offset and effective count, consistent with
   the draw master.
2. `vertex_buffer_contents` — the complete current contents of the selected
   buffer(s), with a packet-sidecar byte file, length and SHA-256. Prefer a
   draw-time authoritative read if the bytes are CPU-accessible; otherwise
   extend the existing L3.1 resource-state tracking. Complete updates replace
   current contents; partial updates patch known ranges; unknown untouched bytes
   remain unknown; retain the provenance of the last contributing operation.
   Do not grow a historical payload arena for superseded values.
3. `matrix_constant` — the exact 64-byte constant slot 0 used by the draw, with
   a packet-sidecar byte file, length and SHA-256.

Additional evidence is welcome but not required for this milestone:
`vertex_attribute_layout`, `render_state_blend`,
`render_state_depth_stencil`, `render_state_rasterizer`, `scissor_state`.
Shader identity/sources, texture and sampler remain missing for L3.2b-II.
Do not claim `packet_ready=true` in this campaign.

## Start from evidence, not a fixed hook list

Read, in this order:

- `analysis/gfx-draw/v0/probe/note.md` and `draw-packet.json`
- `analysis/metal-draw/d1b/gfx-draw-contract.md`
- `analysis/reconstruction/gfx-semantic/v1/draw/_GfxDraw.md`,
  `types.md`, and the vertex-buffer/constant-buffer/set-state dossiers
- `analysis/gfx-resources/v0/note.md` (L3.1 resource identities and semantics)
- `analysis/qualification/v0/primitive-table.evidence.json`
- `analysis/gfx-shader/v0/prerequisite-inventory.json`

The L3.2a packet's 369-byte master snapshot is the starting point. Use it to
identify the bound vertex slot, the constant buffer slot pointer/count and the
selected shader object. Then determine, from the original-code representation,
whether the bytes are CPU-accessible at the draw or must come from creation and
updates. Choose the smallest safe observation. Do not assume a function must be
hooked; a draw-time read may remove the need.

The observation may use at most eight installed targets, with `_GfxDraw`
mandatory. Do not edit `benchmark/eu4_gfx_observe_gateway.S` and do not expand
the gateway pool. Do not implement Metal.

## Frozen evidence contract

The packet is `gfx-draw-packet/v0`. Each input keeps
`{status, evidence[], value?}`. Use only these evidence types:

- `draw_snapshot`: `call_id` (the selected draw), `payload`, `sha256`, `size`,
  `offset`, `length`; the bytes at `offset` must be the claimed value.
- `resource_observation`: `call_id`, `symbol`, `resource_id`, `payload`,
  `sha256`, `size`, `mode` (`complete` or `partial`), `offset`; the capture
  record must match and be structurally valid.
- `static_reconstruction`: `executable_sha256`, `symbol`, `entry_rva`,
  `span_sha256`, `instruction_span`, `interpretation`; semantic interpretations
  are marked review-required and independently reviewed.
- `unresolved`: `reason_code`, `dependency`, `detail`; never counts as
  established.

Required values:

```json
"vertex_buffer_bindings": {
  "status": "established",
  "value": {
    "slots": [{
      "slot": 0, "resource_id": "vb_N", "stride": 24,
      "offset": 0, "count": 4,
      "session_local_header": "0x..."
    }]
  },
  "evidence": [ ... ]
}
```

```json
"vertex_buffer_contents": {
  "status": "established",
  "value": {
    "buffers": [{
      "slot": 0, "resource_id": "vb_N", "stride": 24,
      "offset": 0, "count": 4, "contents_size": 96,
      "contents_sha256": "<64 hex>", "sidecar": "payloads/vb_N.bin",
      "method": "resource_lifecycle" | "draw_time",
      "last_call_id": 12
    }]
  },
  "evidence": [ ... ]
}
```

```json
"matrix_constant": {
  "status": "established",
  "value": {
    "slot": 0, "size": 64, "hex": "<128 hex>",
    "sha256": "<64 hex>", "sidecar": "payloads/matrix.bin",
    "method": "draw_time" | "resource_observation",
    "last_call_id": 13
  },
  "evidence": [ ... ]
}
```

The packet must copy required bytes into its own directory. Hash-only claims do
not pass. Keep all resources from one session; never mix captures or reuse
pointer identities across launches.

## Runtime captures

The campaign launcher owns the budget: three authorized original-game launches
initially. Do not use `--authorize-live`; use `--campaign-run-root`.

Before a launch, write the question and expected observation in `progress.md`
and create `capture-plans/<slug>.json` under the campaign run root:

```json
{"question": "...", "expected_observation": "...", "command": "..."}
```

Use a unique output directory under the run root's `captures/`. From the
workspace, the run root is the workspace parent. Example:

```sh
RUN_ROOT="$(cd .. && pwd)"
python3 -m automation.live_gfx capture \
  --target _GfxDraw --target _GfxSetPrimitiveType \
  --until-target-draw --caller-rva 0xedfcd1 --timeout 120 \
  --output "$RUN_ROOT/captures/<slug>" \
  --campaign-run-root "$RUN_ROOT"
```

Add only the targets needed for the specific question; never exceed eight.
After the capture, build the candidate packet:

```sh
python3 -m automation.live_gfx packet \
  --capture "$RUN_ROOT/captures/<slug>" \
  --output "$RUN_ROOT/packets/<slug>"
```

Inspect `missing`, update `progress.md`, and continue from the checker's
feedback. If the runner is interrupted after a completed capture, resume from
that capture; do not launch again merely because the previous turn stopped.
If the budget is exhausted, stop and report the remaining evidence requirement;
do not edit the counter or reclassify missing inputs as complete.

## Checker feedback

The checker builds and runs your observer, runs frozen observer fixtures, checks
the launch receipts and capture provenance, and independently validates the
packet bytes and field-to-evidence relationships. Candidate tests are regression
coverage only; they do not establish acceptance.

The checker may return:

- `inconclusive` with established/missing inputs and the next investigation;
- `fail` for code/tests, scope, evidence contradiction or missing required bytes;
- `blocked` with `campaign_budget_exhausted` or a corroborated
  `semantic_blocker`;
- `unavailable` for toolchain, oracle, capture or counter integrity failure.

A `BLOCKER:` note line alone is not a semantic blocker. If a dependency cannot
be observed safely, write
`analysis/gfx-draw/v0/l32b/effective-state/blockers.json` with
`reason_code: "semantic_blocker"`, the dependency, and concrete evidence
(static reconstruction or failed observation details). The checker and the
independent reviewer assess it.

## Scope and hygiene

Editable: observer sources/headers/entry registry, startup-trace reader,
packet builder, observer/packet tests, `automation/live_gfx.py` only if needed,
`progress.md`, and `analysis/gfx-draw/v0/l32b/effective-state/**`. New observer
modules must be named `benchmark/eu4_gfx_observe_*.c` so the frozen host build
includes them.

Do not edit: the L3.2a oracle, `analysis/qualification/v0/`, `target.json`,
`benchmark/eu4_gfx_observe_gateway.S`, Metal/worker files, campaign/checker
files, or any path outside the allowed surface. The campaign permissions and
manifest are tamper-detection controls, not a hostile-agent sandbox.

Keep `progress.md` current with established findings, the selected investigation,
capture questions, checker feedback, and a "Next" action.
