# R0 stack attribution protocol (epoch B)

Epoch **B** runs with the R0 probe in **PASSIVE** mode only (no readback). Do not collect `sample` during epoch C.

## Capture

After epoch A completes and while the game remains paused on the Venice fixture:

```sh
sample "$PID" 10 -file "results/<run>/epoch_b.sample.txt" -mayDie
```

Verify flags against the locally installed `sample(1)` manual.

## Analysis

```sh
python3 analysis/tools/r0_stack_analyze.py \
  results/<run>/epoch_b.sample.txt \
  --output analysis/evidence/r0-main-thread-cost-model.json
```

## Attribution rules

The analyzer reconstructs a **weighted inclusive call tree** from `sample` output (including decorated macOS branch lines). For each tree node it computes **exclusive** sample weight (inclusive minus the sum of children). Only exclusive weight is attributed.

Classification uses the **full ancestor path** from root to the node (not each printed line in isolation):

| Dimension | Purpose | Rule application |
|---|---|---|
| Functional caller | Which EU IV renderer or Gfx callsite owns the path | First matching functional rule scanning symbols from leaf toward root |
| Execution location | Where time executes (GL driver, encode, etc.) | First matching execution rule scanning symbols from leaf toward root |

Each attributed exclusive node increments one functional bucket, one execution bucket, and the **cross-tab** joint count `(functional_caller, execution_location)`. Paths with no rule match contribute `unattributed` in that dimension. JSON output normalizes functional and execution marginals to shares; cross-tab retains raw counts.

Example: exclusive samples on a path `CInGameIdler::Render → … → glDrawElements → GLDContextRec::setRenderState` attribute functional `ingame_render` (or a deeper Gfx rule if matched) and execution `gl_driver_set_render_state`, with the joint key recorded in the cross-tab.

Main-thread identification for epoch A↔B mapping uses Mach `THREAD_IDENTIFIER_INFO.thread_id` in [`benchmark/thread_cpu_sampler.py`](../benchmark/thread_cpu_sampler.py) and thread metadata in the `sample` file. Session manifests record `sample_main_thread_corroboration` by matching call-graph thread headers first. If mapping is ambiguous, report `unresolved` in the gate memo.
