# Sampler-uniform experiment: no material benefit

The [completed GOG v1.37.5 run](../results/20260928T081714Z-sampler-uniform/validation.md)
used one unattended, paused A–U–A–U–A session on the pinned Venice fixture.
U suppressed repeated `glUniform1i` values only at the 16 verified calls in
`SShaderOpenGL::SetAll()`; the seventeenth call and all other uniform writes
continued to the driver. The shim cleared its shadow cache on context and
phase changes. Pass-through phases forwarded every targeted call, and the
safety counters recorded no unsafe access.

| U phase | Targeted calls suppressed | Swap-rate change | EU IV CPU per swap | System joules per swap |
|---|---:|---:|---:|---:|
| U1 versus adjacent A phases | 4.87 million (68.6%) | +10.9% | +0.20% | -1.38% |
| U2 versus adjacent A phases | 4.62 million (68.4%) | -0.30% | -0.22% | +2.63% |

The first U phase ran at a higher swap rate and used more CPU time and system
power per second. Normalizing by swaps removes the apparent CPU increase, but
neither U phase shows a convincing CPU saving per frame. Their energy-per-swap
changes point in opposite directions and are small relative to the natural
power variation. The three A phases stayed within 1.21% for CPU time, 3.37%
for combined system power, and 1.21% for swap rate. Thus the experiment
demonstrates effective call suppression, **not a useful performance win**.

All five captured screenshots show the same paused Venice scene, with no
obvious rendering defect in those samples. This is a sampled visual check, not
a proof that every possible game state renders correctly. The disposable save
was unchanged. Power is system-wide SoC power, so this one session cannot
attribute a small energy difference solely to EU IV.

Do not patch `SShaderOpenGL::SetAll()` in the executable on this evidence or
ask for another game run to refine this low-yield optimization. The independent
[static draw-path analysis](render-buckets.md) identifies one
`GfxDrawIndexed` call per mesh subrecord and object constants immediately
before each draw. The next useful work is offline analysis of how those
subrecords are built and whether any genuinely compatible draws exist; a
future game run should be requested only if that analysis yields a specific,
testable intervention.
