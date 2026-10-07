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

Each stack line is classified independently in **two dimensions**:

| Dimension | Purpose | Mutual exclusivity |
|---|---|---|
| Functional caller | Which renderer or Gfx callsite | First matching rule in the functional list |
| Execution location | Where time is spent (GL driver, encode, etc.) | First matching rule in the execution list |

A line may contribute to one bucket in each dimension. Use the **cross-tab** for joint counts. Incomplete lines remain `unattributed` in that dimension.

Main-thread identification for epoch A↔B mapping uses Mach `THREAD_IDENTIFIER_INFO.thread_id` in [`benchmark/thread_cpu_sampler.py`](../benchmark/thread_cpu_sampler.py) and thread metadata in the `sample` file. If mapping is ambiguous, report `unresolved` in the gate memo.
