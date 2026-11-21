# Cache profile on sealed present 904

Offline measurement of the shader-translation cache, the native Metal
function/pipeline path, and the hashing that feeds them. No EU4 launch, no
tracked-file edits, no changes to sealed capture data.

- Machine: Apple Silicon (`arm64`, Apple M5 Max), macOS; Python 3.14.10.
- Measured: 2026-11-20 23:58 – 2026-11-21 00:15 (Europe/Berlin).
- Repetitions: 3 per condition, alternating order, medians and ranges reported.
  No regression/improvement claim is made from these numbers.
- Evidence: [`measurements-present-904.json`](evidence/cache-profile/measurements-present-904.json),
  [`measurements-present-916.json`](evidence/cache-profile/measurements-present-916.json),
  [`validation.json`](evidence/cache-profile/validation.json),
  harness [`measure.py`](evidence/cache-profile/measure.py) /
  [`validate.py`](evidence/cache-profile/validate.py).

## Headline numbers (present 904, medians of 3)

| Phase | Median | Range | Notes |
|---|---:|---:|---|
| Cold translation, empty cache root | **3.211 s** | 3.207–3.313 | 17 programs / 34 stage entries / 33 compiler inputs |
| Warm translation-cache lookup | **0.374 s** | 0.364–0.376 | cache verified, accepted, copied; no tool processes |
| Plain translation, no `--cache-dir` | 3.310 s | 3.188–3.334 | cold-looking translation without cache publish |
| Hash: cache identity (`_shader_source_identity`) | **26.8 ms** | 19.2–29.1 | 3,771,330 bytes; 99.9 % of it is tool binaries |
| Hash: first trace load + full capture verification | **258 ms** | 257.6–258.8 | 1,307 blobs / 460,636,055 B + 7,720,704 B reference |
| Native host, full 327-event replay | **0.603 s** | 0.599–0.758 | new process each run; compiles every MSL function again |
| Native host, startup → no encode | 0.113 s | 0.112–0.119 | process start + frame parse, `--stop-after 0` |
| Native host, startup → first draw encoded | 0.126 s | 0.123–0.154 | first MSL compile + pipeline + GPU wait |
| GPU service time | **unknown** | — | not recorded anywhere in the replay host |

## 1. Workload identity

Sealed present 904, capture C:

| Field | Value |
|---|---|
| Frame dir | `.local/m7/goal-round1/capture-c/frame` |
| Session | `afd444f2-6478-459f-8db6-e565c49dbd63` |
| Present / scene index | 904 / 904 |
| `frame-trace.json` sha256 | `faf201f01583daf54a44a06432c3be674f00aca3bc7f20736b6986ec69d3027d` |
| `frame-seal.json` sha256 | `889bf02531216a2da7c7ab7a529a40801b77cd3b846ecc86f653a572af9e8e2f` |
| Event stream | `frame-events.jsonl`, 9,219,432 B, sha256 `8801f037…3dd547` |
| Events | 327 (317 draws, 9 clears, 1 copy) |
| Target | 1728 × 1117, BGRA8 |
| GL reference | 7,720,704 B, sha256 `d0639358…208dda` |
| Existing shadow artifacts | `.local/m7/goal-round1/capture-c/shadow/` (`shaders/`, `replay.json`) |

Translation result on this frame (current translator):

- **17 programs**, **34 stage entries** (17 vertex + 17 pixel), **33 distinct
  compiler inputs**: programs 367 and 370 share one identical linked vertex
  input and therefore one MSL/GLSL/SPV file.
- 99 payload files: 33 `.glsl` (549,978 B), 33 `.spv` (272,344 B), 33 `.metal`
  (154,043 B), 976,365 B total.
- Cache entry published under the identity key: 103 files, 1,320,139 B
  (`shaders.json`, `translation.json`, `shader-cache.json`,
  `translation-cache.json` + the 99 payload files).

The current translation was checked against the sealed shadow payload:
stage digests are identical for all 17 programs, the MSL and GLSL **content
sets** are identical (32 unique MSL contents, 33 unique GLSL), and the replayed
Metal frame is bit-identical to the sealed one
(`b73cac94…10ba6f`). The `.metal` **filenames** differ because the sealed
artifacts were produced by an older translator generation that keyed output
paths by the raw source digest; the current one keys by the linked compiler
input ([`automation/gfx_shader_translate.py:466`](../../automation/gfx_shader_translate.py#L466)).
The sealed `translation-cache.json` records the older
`gfx-shader-source-identity/v1` identity (`567ae14b…7a9874`) with a
size+mtime translator record; the current identity is v2 (content hashes,
attribute locations) and is `0e057ad5…a00637f`. See
[`validation.json`](evidence/cache-profile/validation.json).

## 2. Cache mechanisms

### 2.1 Shader-translation cache (exists, content-addressed)

The cache is a Python-side, cross-invocation translation cache.

- **Identity** ([`automation/m6_frame.py:1580`](../../automation/m6_frame.py#L1580)):
  `_shader_source_identity` projects every draw's program name to its stage
  digests and to the attribute-name → location map
  ([:1611](../../automation/m6_frame.py#L1611)), adds `_translator_identity`
  ([:1552](../../automation/m6_frame.py#L1552)), and hashes the canonical JSON
  ([:1618](../../automation/m6_frame.py#L1618)). It deliberately excludes frame or
  event identity, so two frames with the same programs share one entry.
- **Translator identity** hashes the actual `gfx_shader_translate.py` bytes and
  both external tool binaries (glslangValidator, spirv-cross), not a
  size/mtime fingerprint ([:1560](../../automation/m6_frame.py#L1560),
  [:1573](../../automation/m6_frame.py#L1573)).
- **Cache root** = `--cache-dir` + identity sha256
  ([:1757](../../automation/m6_frame.py#L1757), [:1767](../../automation/m6_frame.py#L1767);
  CLI option at [`automation/m6_frame.py:3449`](../../automation/m6_frame.py#L3449)).
- **Hit acceptance** is content-verified, not file-existence:
  `_translation_cache_valid` checks the metadata schema, the recorded identity,
  `shaders.json` schema/`ok`, and every stage's `glsl_file`/`spv_file`/
  `msl_file` presence and sha256
  ([:1643–1683](../../automation/m6_frame.py#L1643)). The warm path validates the
  cache root, copies it, then validates the staged copy again
  ([:1771–1784](../../automation/m6_frame.py#L1771)).
- **Miss path** translates, writes cache metadata, validates the output, and
  publishes atomically through a unique staging directory
  ([:1785–1801](../../automation/m6_frame.py#L1785),
  [:1693](../../automation/m6_frame.py#L1693)). An invalid winner is quarantined and
  replaced; `--force` bypasses adoption.
- The contract is pinned by
  [`tests/test_m7_translation_cache.py:175`](../../tests/test_m7_translation_cache.py#L175)
  (second run is a hit), `:226` (missing stage rebuilt), `:242` (corrupted
  stage rebuilt), and `:257` (corrupt published entry repaired).

Note what the key does **not** include: frame/event identity, sampler state,
texture state, render state. Translation is deterministic in the linked stage
programs, which is why present 904 and present 916 get the same key (section 5).

### 2.2 Native Metal functions and pipelines (in-process only, no archive)

The replay host keeps both caches only for the lifetime of one process:

- Function cache: `g_functions` is an `NSMutableDictionary` created empty per
  session ([`benchmark/eu4_gfx_frame_replay.mm:5536`](../../benchmark/eu4_gfx_frame_replay.mm#L5536));
  `function_for` reads the MSL bytes, hashes them, and keys the dictionary by
  `msl sha256 + entry + stage kind + compile-options`
  ([:2105](../../benchmark/eu4_gfx_frame_replay.mm#L2105),
  [:2162–2170](../../benchmark/eu4_gfx_frame_replay.mm#L2162)).
  On a miss it creates a library with `newLibraryWithSource`
  ([:2182](../../benchmark/eu4_gfx_frame_replay.mm#L2182)), resolves the entry with
  `newFunctionWithName` ([:2193](../../benchmark/eu4_gfx_frame_replay.mm#L2193)), and
  stores it ([:2202](../../benchmark/eu4_gfx_frame_replay.mm#L2202)).
- Pipeline cache: `g_pipelines`, also created empty per session
  ([:5535](../../benchmark/eu4_gfx_frame_replay.mm#L5535)), keyed by a state key
  ([:2210](../../benchmark/eu4_gfx_frame_replay.mm#L2210)), populated with
  `newRenderPipelineStateWithDescriptor`
  ([:2690–2701](../../benchmark/eu4_gfx_frame_replay.mm#L2690)).
- There is **no persistent native cache** in this host: no `MTLBinaryArchive`,
  no `.metallib`, no `newLibraryWithFile`/`newLibraryWithData`, no archive
  serialization anywhere in
  [`benchmark/eu4_gfx_frame_replay.mm`](../../benchmark/eu4_gfx_frame_replay.mm).
  Every replay process recompiles the MSL and recreates its pipelines.
- The in-process lookup itself is not free: `function_for` is called for both
  stages of every draw ([:2253](../../benchmark/eu4_gfx_frame_replay.mm#L2253)), and
  each call re-reads and re-hashes the MSL file
  ([:2105–2121](../../benchmark/eu4_gfx_frame_replay.mm#L2105)). For present 904 that
  is **634 lookups / 5,276,365 bytes of MSL re-hashed per replay**, even on a
  dictionary hit.

So "cold native" is every run. A warm translation cache saves the
glslang/spirv-cross work but never the Metal compile/pipeline work.

### 2.3 GPU time is not recorded

The replay host commits one command buffer per event, waits for completion
([`:4878`](../../benchmark/eu4_gfx_frame_replay.mm#L4878),
[`wait_for_transfer` `:3592`](../../benchmark/eu4_gfx_frame_replay.mm#L3592)), and
reports `command_status == 4` (completed), but it never reads
`GPUStartTime`/`GPUEndTime`. The success-path `replay.json` has **no timing
key at all** (the scan returned `[]`); only the C ABI fills
`out_result->duration_ns`
([`:5425`](../../benchmark/eu4_gfx_frame_replay.mm#L5425)) and the failure path writes
`duration_ns` into JSON ([:4541](../../benchmark/eu4_gfx_frame_replay.mm#L4541)).
The only GPU timing in the tree is the live perf harness
([`benchmark/eu4_gfx_perf.mm:72–86`](../../benchmark/eu4_gfx_perf.mm#L72)), which is
not linked into the frame replay host. Hence GPU time is reported as
**unknown**, not estimated.

## 3. Measurement method

All writes go under
`analysis/harness-remediation/evidence/cache-profile/`; the sealed frame and
its existing shadow dir were read-only. The replay host was already built and
its dependency fingerprint matched, so no native build occurred
(`_build_replay_host_locked` returns the existing host at
[`automation/m6_frame.py:1476`](../../automation/m6_frame.py#L1476); binary sha256
`2866e4cd…b2824e`).

**Cold** (fresh output and fresh cache root, every repetition):

```sh
python3.14 -m automation.m6_frame translate \
  --frame .local/m7/goal-round1/capture-c/frame \
  --out <fresh>/out --cache-dir <fresh>/cache
```

**Warm** (same cache root, fresh output):

```sh
python3.14 -m automation.m6_frame translate \
  --frame .local/m7/goal-round1/capture-c/frame \
  --out <fresh>/out --cache-dir <same>/cache
```

**Plain** (no cache root): the same command without `--cache-dir`.

**Native host**, fresh output per run:

```sh
benchmark/.build/gfx_frame_replay \
  --frame .local/m7/goal-round1/capture-c/frame \
  --shaders <warm>/out --out <fresh>
# plus --stop-after 0 (startup only) or --stop-after 2 (through first draw)
```

Ordering: rep 1 cold → warm → plain; rep 2 warm (cache from rep 1) → cold →
plain; rep 3 cold → warm → plain. Host: full/startup alternated. Wall time is
`time.perf_counter()` around `subprocess.run`; hash microbenchmarks are
in-process medians of 7 samples; fresh-process trace-load was measured in 3
separate Python processes because the blob digest cache
([`automation/m6_frame.py:76`](../../automation/m6_frame.py#L76)) makes later calls in
one process unrepresentative.

**System load / contention.** The machine was busy for the whole series:
1-minute load average 6.8–7.6 (40 probes). Concurrent CPU consumers observed:
ChatGPT/Codex framework up to 90 %, Firefox GPU Helper up to 88 %, another
Python 3.14 process up to 83 % (the `closure-base` offline test run), clang up
to 62 %, `Metadata`/`FSEvents` up to 68 %, Cursor fileWatcher up to 56 %,
WindowServer 55 %, Firefox content processes 49 %. At 00:02 Baldur's Gate 3 was
observed at 437 % CPU, before the timed series. Absolute wall times below are
therefore inflated and noisy; cold and warm were interleaved so both see the
same conditions. Ranges are reported instead of single values.

## 4. Results

### 4.1 Translation: cold vs warm vs plain (per repetition)

| Rep | Order | Cold (s) | Warm (s) | Plain, no cache (s) | Warm `hit` | Warm `publish` |
|---:|---|---:|---:|---:|---|---|
| 1 | cold → warm | 3.2114 | 0.3758 | 3.1884 | true | null |
| 2 | warm → cold | 3.3135 | 0.3638 | 3.3342 | true | null |
| 3 | cold → warm | 3.2066 | 0.3737 | 3.3104 | true | null |
| | **median** | **3.2114** | **0.3737** | **3.3104** | | |

Cold/warm ratio from medians: **8.59×** (8.55–9.11× across the three paired
repetitions).

Warm identity acceptance (not just "`shaders.json` exists"):

- `translation_cache.hit == true`, `publish == null`;
- cache key in the command output equals the independently computed identity
  (`0e057ad5…a00637f`);
- `_translation_cache_valid(cache_root, identity) == true` and
  `_translation_cache_valid(staged_out, identity) == true`;
- shader payload (`shaders.json`, all GLSL/SPV/MSL) is byte-identical between
  cold and warm output; only `translation-cache.json.hit` differs.

Cold publishes `publish: "published"` every time; the cache root is complete
and valid after each cold run (103 files, 1,320,139 B).

Derived cold budget (independent medians, not one simultaneous run):
module start 41 ms + trace load/verify 258 ms + identity 27 ms + translation
~2.86 s (66 glslang/spirv-cross processes) + validate/publish ~27 ms ≈ 3.21 s.
The plain (no cache) median being within the cold range spread shows the cache
publish/validation overhead is small (~25–30 ms), not the reason cold is slow.

### 4.2 Hashing cost on authentic inputs

All rows are in-process medians of 7 samples, except the cache copy
(5 samples) and the fresh-process `load_trace` (3 processes).

| Hash unit | Bytes hashed | Files | Median | Range |
|---|---:|---:|---:|---:|
| Cache identity, full `_shader_source_identity` | 3,771,330 | — | 26.78 ms | 19.18–29.12 ms |
| ↳ translator identity only (tool binaries + translator) | 3,766,785 | 3 | 19.01 ms | 18.23–20.69 ms |
| Warm cache validation (`_translation_cache_valid`) | 976,365 | 99 | 3.68 ms | 2.68–4.09 ms |
| Receipt artifact digest (`artifact_digest`) | 1,320,139 | 103 | 3.08 ms | 2.86–3.74 ms |
| 33 shader sources, hash only | 544,615 | 33 | 0.17 ms | 0.17–0.18 ms |
| 33 shader sources, read + hash | 544,615 | 33 | 0.70 ms | 0.63–0.89 ms |
| 33 MSL artifacts | 154,043 | 33 | 0.51 ms | 0.49–0.74 ms |
| Cache copy (context, not hashing) | 1,320,139 | 103 | 20.26 ms | 16.44–21.90 ms |
| **First `load_trace` in a fresh process** | **468,356,759** | **1,308** | **258 ms** | 257.6–258.8 ms |

The last row is the dominant hashing cost of a warm lookup: before the cache
identity can be computed, the command loads the trace and re-verifies every
referenced blob (1,307 files, 460,636,055 B) plus the 7,720,704 B GL reference
([`automation/m6_frame.py:108`](../../automation/m6_frame.py#L108),
[`:160`](../../automation/m6_frame.py#L160)). The shader-identity hashing itself is
only 3.77 MB, and 3.57 MB of that is the `spirv-cross` binary. Within one
process later `load_trace` calls drop to ~78 ms because the blob digests are
memoized ([`automation/m6_frame.py:76`](../../automation/m6_frame.py#L76)); a cache
lookup starts a new process, so it pays ~258 ms.

Derived warm-lookup budget (independent medians): module start 41 ms + trace
load/verify 258 ms + identity 27 ms + validate/copy/validate 3.7+20.3+3.7 ms
≈ 354 ms of the measured 374 ms; the remainder is JSON writes and process
teardown. **The warm path is dominated by trace verification and parsing, not
by cache-key hashing or artifact validation.**

### 4.3 Native host (new process per run)

| Rep | Full replay (s) | Startup, no encode (s) | Startup → first draw (s) | Result |
|---:|---:|---:|---:|---|
| 1 | 0.6025 | 0.1120 | 0.1538 | ok, 327 encoded, 0 failed |
| 2 | 0.5993 | 0.1129 | 0.1227 | ok, 327 encoded, 0 failed |
| 3 | 0.7578 | 0.1189 | 0.1263 | ok, 327 encoded, 0 failed |
| | **median 0.6025** | **0.1129** | **0.1263** | `command_status = 4` |

- Fixed startup (process + 9.2 MB frame JSON + seed) is ~113 ms; reaching the
  first encoded draw adds ~7–42 ms (median delta 9.9 ms) for two MSL functions,
  one pipeline, one submit and GPU wait.
- The remaining ~0.48 s covers the other 325 events, the other 16 programs'
  compiles/pipelines, per-event GPU waits, per-draw MSL re-hashing (5.3 MB),
  and the final readback/write. Native compile cost cannot be separated from
  GPU service time externally.
- The supported CLI path (Python + build check + host) took 0.476 s for one
  run; the difference from the raw host is Python start plus the build-stamp
  check.
- Because the host has no persistent native cache, this ~0.60 s native cost is
  paid identically after a cold or a warm translation-cache lookup.

Bit-exactness: the full replay's `metal-frame.bgra` is identical to the sealed
shadow artifact (`b73cac94…10ba6f`), so the measured native path is the
authentic one. (The sealed shadow report's own `metal.duration_ns` is
2,584,629,000 ns in a checkpointed run with 10 checkpoint readbacks; it is not
comparable to these plain replays.)

### 4.4 GPU execution

| Metric | Value | Evidence |
|---|---|---|
| GPU service time | **unknown** | no `GPUStartTime`/`GPUEndTime` read in the frame replay host; success `replay.json` has no timing key |
| Instrumentation needed | `addCompletedHandler:` summing `GPUStartTime`/`GPUEndTime` per committed command buffer, published in `replay.json`; or Metal System Trace / `MTLCounterSampleBuffer` | existing pattern at [`benchmark/eu4_gfx_perf.mm:72`](../../benchmark/eu4_gfx_perf.mm#L72) |

The one-command-buffer-per-event design
([`benchmark/eu4_gfx_frame_replay.mm:4878`](../../benchmark/eu4_gfx_frame_replay.mm#L4878))
means GPU time is serialized into the 0.60 s wall time, but its share is not
observable with the current host.

## 5. Secondary check: sealed present 916

`.local/m7/goal-round1/capture-b/frames/frame-000001` is sealed and
complete (session `8ee43ade-fb4e-4873-b243-0ff704b225ce`, 326 events, 316
draws, 1728 × 1117). It was measured the same way, separately.

| Metric | 916 median | 916 range | 904 median |
|---|---:|---:|---:|
| Cold translation | 3.207 s | 3.187–3.258 | 3.211 s |
| Warm lookup | 0.344 s | 0.340–0.345 | 0.374 s |
| Plain, no cache | 3.236 s | 3.210–3.260 | 3.310 s |
| Cache identity | `0e057ad5…a00637f` | — | `0e057ad5…a00637f` |
| Native full replay | 0.395 s | 0.366–0.399 | 0.603 s |
| Startup → first draw | 0.108 s | 0.105–0.115 | 0.126 s |
| Replayed Metal frame | `5143a17a…86a732` = sealed | — | `b73cac94…10ba6f` = sealed |

The two presents produce the **same cache identity**: all 17 program/stage
digests and attribute-location maps are identical, so one translation entry
serves both. This is the frame-independent contract working on real data, not
a claim that every present will collide. The existing 916 shadow cache key
(`567ae14b…`) differs only because it predates the current translator identity
scheme. The 916 series ran at a slightly lower load average (7.0 vs 7.6)
and has one fewer event; with the same shader set, treat the host-time
difference as environmental, not a workload effect.

## 6. Interpretation

1. **The translation cache is real and saves ~2.84 s per warm frame** (8.6× on
   the translate command). The saving is almost entirely the 66 external
   glslang/spirv-cross processes (2 × 33 stages); cache validation and copy
   together are ~28 ms.
2. **Warm lookup is not cheap in absolute terms** (0.37 s) because it re-loads
   and cryptographically verifies the whole capture (468 MB, 258 ms) and
   re-computes the identity (27 ms) before it can accept the cache. The cache
   check itself (0.98 MB, 3.7 ms) and copy (1.3 MB, 20 ms) are minor.
3. **Hashing the cache key is dominated by tool binaries**, not shader data:
   3.57 MB `spirv-cross` + 0.16 MB `glslangValidator` + 32 KB translator =
   3.77 MB, versus 0.54 MB of shader sources. Any attempt to make warm lookup
   cheaper should look at trace verification and translator-identity hashing,
   not at the 33 source digests.
4. **Native Metal work has no persistent cache.** Each replay process recompiles
   all MSL and rebuilds pipeline state; the in-process dictionaries only help
   within one process (634 stage lookups re-hash 5.3 MB of MSL per replay).
   A "cold build" in the task's sense is ~3.21 s translation + ~0.60 s native
   process = ~3.81 s; a "warm" cycle is ~0.37 s + ~0.60 s = ~0.97 s
   (≈3.9× end-to-end), and the native half is unchanged by the translation
   cache.
5. **GPU time is unknown**; no number is inferred. The host's per-event
   `waitUntilCompleted` means GPU service time is inside the 0.60 s wall, but
   separating it requires host instrumentation (Metal System Trace or
   `GPUStartTime`/`GPUEndTime` handlers).

## 7. Limitations

- **Contention.** Load average 6.8–7.6 and multiple CPU-heavy processes
  (Codex 90 %, Firefox GPU helper 88 %, `closure-base` test Python 83 %,
  clang 62 %) ran throughout. Absolute values are inflated; medians/ranges are
  reported and cold/warm were interleaved, but run-to-run spread reaches 25 %
  (e.g. one 0.758 s host replay).
- **n = 3** per condition. The cold/warm separation is large (8.5–9.1×) and
  consistent, but no small difference (for example cold vs plain, or the
  effect of publish overhead) is claimed as significant.
- **Wall clock only.** Subprocess wall time includes Python start and tool
  process creation; CPU time was not separated.
- **External timing only for native work.** Startup→first-draw is a
  `--stop-after` bound, not an instrumented encode timestamp; compile time,
  GPU time, and readback time are not individually observable. Partial runs
  are timing probes, not pixel-qualified replays.
- **Single machine / single Metal device.** Metal driver-level caching across
  processes is not excluded by the source (the host itself has none). An
  exploratory `MTL_SHADER_CACHE_DISABLE=1` A/B was inconclusive (0.39–0.61 s
  vs 0.60–0.76 s), and that switch is undocumented, so this remains an open
  question rather than a claim.
- **Translator generation.** The sealed shadow artifacts were produced by an
  older translator identity (v1, size+mtime based). The current v2 translator
  reproduces byte-identical stage payloads and a bit-identical Metal frame on
  both presents, but cache keys from the sealed run are not reused.
- The 916 comparison is a second sealed present, not a controlled A/B: it was
  run later under different contention.

## 8. Reproduction

```sh
python3.14 analysis/harness-remediation/evidence/cache-profile/measure.py \
  --label present-904 --frame .local/m7/goal-round1/capture-c/frame --reps 3
python3.14 analysis/harness-remediation/evidence/cache-profile/measure.py \
  --label present-916 --frame .local/m7/goal-round1/capture-b/frames/frame-000001 --reps 3
python3.14 analysis/harness-remediation/evidence/cache-profile/validate.py
```

The scripts are read-only against the captures, write only under
`analysis/harness-remediation/evidence/cache-profile/`, and reuse the existing
replay host (no rebuild). `work/` holds throwaway output dirs; the JSON files
hold every per-repetition sample.
