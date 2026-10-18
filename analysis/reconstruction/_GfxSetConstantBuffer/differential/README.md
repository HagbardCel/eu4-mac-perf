# RE2 differential validation — `_GfxSetConstantBuffer`

**Status:** complete (2026-10-18). Frozen inputs: [`scenarios.json`](scenarios.json). Reference
record: [`reference.json`](reference.json). Comparator and reference validator:
`reverse_engineering/reconstruction_diff.py`; guards: `tests/test_reconstruction_differential.py`;
runner and emulator script: RE0's, unchanged
([`pilot.md`](../pilot/pilot.md), `reverse_engineering/reconstruction_pilot.py`,
`reverse_engineering/ghidra/EmulateGfxSetConstantBuffer.java`).

**Scope:** offline only. Nothing here launches EU IV, opens a game process or attaches to a binary.
Read [What this establishes](#what-this-establishes-and-what-it-does-not) before citing it.

## What it compares

Two descriptions of the same 21 original instructions (entry RVA `0x15ec36a`, pinned image
`b3d38876…141d`):

| Side | What it is | How it is driven |
| --- | --- | --- |
| **Original** | RE0's P-code emulator executing the pinned binary's own decoded instructions under constructed state | `scenarios.json` row → `reference.json` row |
| **Reconstruction** | RE1's C++20 reimplementation ([`../reconstruction/`](../reconstruction/README.md)) | `harness scenario <slot> <selector> <dword\|absent>`, pre-state on stdin |

The reference record is the emulator's own observation of the original on these inputs, two
independent runs byte-identical (canonical SHA-256 `e46855dc…`). The reconstruction is compiled
fresh by the test module (sanitized, `-O0`) and reconciled with it row by row.

## The corpus

414 rows: the cross product of the six declared slots with two frozen input sets, so every
identified state class (both control-flow paths, all six slot positions and the 16-byte stride) meets
every input class.

| Set | Values | Rows per slot |
| --- | --- | --- |
| Buffer-supplied length (`R9D == -1`) | `0x00000000..0x00000010`, `0x7ffffffc..0x80000003`, `0xfffffffc..0xffffffff` | 29 |
| Explicit selector (`R9D != -1`) | `0x00000000..0x00000010` (signed `-16..16`), `0x7ffffffc..0x80000003`, `0xfffffff0..0xffffffff` | 40 |

The boundaries are the sign and wrap boundaries of both arithmetic paths. `0x7ffffffd` as a buffer
length is where a signed division on the buffer path departs from the original's logical shift, and
every selector whose `+3` sum changes the truncating division (`0x00000001`, `0x00000005`,
`0x7ffffffd`, the negative band) is where the missing `+3` wrap departs. The explicit-selector rows
declare no buffer, as RE0's `s13` did: that path must never read `[RSI]`, so a stray read would be
caught as an uninitialized dependency.

The RE0 pilot's 13 frozen scenarios are a subset of this corpus, at the same constructed state
(a test asserts it). Their expectations were hand-derived and frozen before any execution code
existed, so that part of this corpus is anchored to something the emulator did not produce — the
rest is anchored to the emulator alone.

`path` on a row is a label the emulator echoes for its own result; it is derived from the input and
re-derived by the validator rather than trusted.

## What is compared, and how

Row inputs are identical on both sides, and both sides start from the same byte pattern, so a
byte-level reconciliation is meaningful. It is:

- **byte-exact** for the word count (`ctx+0xc8+16·slot`, 4 bytes) and the dirty byte
  (`ctx+0x128`, 1 byte);
- **a role** for the stored buffer pointer (`ctx+0xd0+16·slot`, 8 bytes): the emulator stores the
  constructed buffer address and the reconstruction a host pointer, so the field is masked on both
  sides and each side must independently show the pointer *is* the buffer argument (the store trace
  for the original, the harness's `buffer=ARG` for the reconstruction);
- **contained** on both sides: neither may change a byte outside those three fields, and outside the
  masked pointer field the two traces must change exactly the same bytes;
- **extended to the whole common region**: the compared bytes are `[ctx+0xb8, ctx+0x130)` — the
  emulator's observation window `[ctx+0xb8, ctx+0x139)` intersected with the reconstruction's
  `0x130`-byte object. A test asserts every declared field lies inside that region, so a layout
  change cannot silently drop one;
- **RAX** as an observed outcome on both sides (the context base), not a declared return type;
- **fail-closed**: a row whose recorded original is not qualified, or whose recorded run read
  uninitialized constructed state, is refused rather than compared — agreement with a broken
  reference would be evidence of nothing.

Not compared: the original's stack frame (`PUSH RBP`/`RET`) and its frame-pointer traffic, which the
reconstruction does not model; the emulator's constructed bytes beyond `ctx+0x130`; and incidental
register traffic other than `RAX`.

## The two qualification modes

RE0's pilot froze **expectations** before execution and its validator asserts them exactly. This
corpus freezes **inputs only**: there is nothing here to fit, and nothing to fit it to. What makes
the reference evidence is therefore different, and `reconstruction_diff.validate_reference` checks
exactly that:

- the record was produced from *this* corpus (`scenarios_sha256` over the corpus bytes) and from the
  pinned binary (`binary_sha256`, checked against `reverse_engineering/target.json`);
- two independent runs produced the same canonical block, which hashes to its own recorded digest;
- every row is the corpus's own input in the corpus's own order, carries the path label its input
  selects, is qualified with no reasons, read no uninitialized state, and shows the three declared
  writes in its store trace.

It deliberately does **not** compare a recorded byte value with a frozen one — it has none. A
byte-level forgery of a recorded window is caught by the *comparison* instead, which is why the
controls below include both kinds.

## Controls

None of these is a claim that the miss cannot occur; each is a check that the gate bites.

- **Near misses (both RE1 arithmetic alternatives).** Two deliberately wrong reconstructions are
  built from mutated copies of the tracked header — nothing tracked is edited, and the mutation
  anchor must be present or the test fails — and must be rejected, each only on its own path:
  a signed division on the buffer path (48 of 414 rows) and the missing `+3` wrap on the selector
  path (168 of 414 rows). The faithful build passing the same comparison is what makes these
  non-vacuous.
- **A perturbed reference row.** The recorded original is the reference, so altering it must break
  the comparison: a flipped byte in the recorded word count, and a flipped canary byte the original
  never wrote, are both reported.
- **A refused reference row.** A row marked unqualified, or missing from the record, is reported and
  never compared.
- **A forged record.** Records from another corpus or another binary, dropped or reordered rows, a
  changed recorded selector, a wrong path label, a missing declared store, a changed label and a
  changed limits list are each rejected for the reason under test (the forgeries are re-signed, so
  the digest check cannot be what catches them).
- **A withheld required input (a real run).** A buffer-path row with its buffer removed is re-run
  through the emulator: the load is recorded as an uninitialized dependency, the run marks itself
  unqualified with a reason, and the validator rejects the record. The explicit-path rows, which
  declare no buffer and qualify, are the complementary positive case.

## What this establishes, and what it does not

It establishes that, on 414 frozen inputs, RE1's reconstruction produces **the same observable
effect on the context object** — the same three writes with the same values, the same absence of any
other change, the same `RAX` — as the original's decoded instructions do inside a Ghidra P-code
emulator. The reference record carries this statement and the frozen limits verbatim, so the
evidence cannot be read as more than it is.

It does **not** establish any of the following, and these limits travel with the evidence:

1. P-code emulation is not hardware-level execution of x86 machine code, and this is not runtime
   behaviour inside EU IV.
2. Only `slot ∈ [0, 6)` is covered; `slot ≥ 6` remains an open caller invariant, as does whether
   callers consume `RAX`.
3. Agreement on these 414 inputs is not a proof of equivalence on every input. The input sets are
   the paths' sign and wrap boundaries, not an exhaustive enumeration.
4. The pointer field is compared as a role; the two stored pointer *values* are not comparable and
   are not compared.
5. The original's stack traffic and its exact instruction-level write ordering are outside this
   comparison (see RE1's own limits).
6. No claim is made about `_GfxSetConstantBuffer`'s importance as a rendering bottleneck.

## Reproducing

The corpus run needs Ghidra and the analysed project; the comparison needs a C++20 compiler. The
committed reference is validated by CI with neither.

```sh
export EU4_GHIDRA_WORK=~/GhidraEU4   # project location, as reverse_engineering.paths resolves it
GHIDRA_INSTALL=/opt/homebrew/opt/ghidra/libexec \
  PYTHONPATH=benchmark:. .venv/bin/python -m unittest tests.test_reconstruction_differential -v
```

That module re-runs the corpus twice and asserts the committed canonical block is reproduced, runs
the 414-row comparison against the sanitized build of the reconstruction, builds and rejects the two
near misses, and exercises the controls above. The reference itself is captured with:

```python
from pathlib import Path
from reverse_engineering import reconstruction_diff as diff
reference = diff.run_twice(Path("/tmp/re2"))   # diff.SCENARIOS_PATH through RE0's runner, twice
```

## Provenance

`scenarios.json` declares inputs and paths, never outcomes, so no expectation could be fitted to an
observed run. `reference.json` records what the emulator observed on those exact bytes; the two are
bound by `canonical.scenarios_sha256`, and a test fails if the corpus is edited without re-running
the oracle. A mismatch between the two sides is to be resolved by amending the contract or the
corpus in a separate, reviewed commit — never by editing a recorded observation to match the
reconstruction, and never by relaxing the comparison to fit.

The emulator script, the runner and the constructed state are RE0's, unchanged: this corpus was run
by the code that produced [the RE0 receipt](../pilot/observations-2026-10-18.json), extended only
with the corpus path as a parameter.
