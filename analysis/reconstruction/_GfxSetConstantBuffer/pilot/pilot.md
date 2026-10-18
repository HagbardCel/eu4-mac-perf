# RE0 offline execution pilot — `_GfxSetConstantBuffer`

**Status:** complete (2026-10-18). Receipt: [`observations-2026-10-18.json`](observations-2026-10-18.json).
Frozen inputs: [`scenarios.json`](scenarios.json). Runner and validator:
`reverse_engineering/reconstruction_pilot.py`; emulator script:
`reverse_engineering/ghidra/EmulateGfxSetConstantBuffer.java`; guards:
`tests/test_reconstruction_pilot.py`.

**Scope:** offline only. Nothing here launches EU IV, opens a game process or attaches to a
binary. The emulator interprets the pinned binary's own decoded instructions under a state this
harness constructs; read [What this establishes](#what-this-establishes-and-what-it-does-not)
before citing it.

## What ran

The 21 reviewed instructions of the function (entry RVA `0x15ec36a`, 75 bytes, image base
`0x100000000`) were executed in Ghidra 12.1.4's P-code emulator, in two frozen paths:

| Path | Selector | Instructions | Bytes | Final instruction |
| --- | --- | --- | --- | --- |
| `length_from_buffer` | `R9D == -1` — the length is read from `[RSI]`, then a logical `SHR` | 15 | 55 | `RET` at `0x15ec3b4` |
| `explicit_length` | `R9D != -1` — `CMOVNS`/`SAR` divide-by-4 idiom | 18 | 64 | `RET` at `0x15ec3b4` |

Thirteen scenarios cover them: one per slot in `[0, 6)` at a fixed length (exercising the
16-byte stride and the closure of the declared observation region), four buffer-supplied
lengths including two that carry into the sign bit (`0xfffffffc`, `0x7ffffffd`), and four
explicit selectors including the `CMOVNS` overflow (`0x7fffffff`) and a negative one (`-8`).
Scenario `s13` deliberately leaves the buffer region uninitialized: it qualifies only if the
explicit-selector path never reads `[RSI]`.

The constructed state is explicit, complete and **declaration-driven**: the emulator builds the
register file, the context fill pattern and the buffer from `scenarios.json` itself, and the
validator reconstructs the context and stack pre-state from the same declarations and compares the
recorded pre-state to it byte-for-byte. `R9` starts at the scenario's `r9d` zero-extended — only
its upper 32 bits are zero, and `RDX` starts at zero — so the frozen expectations do not depend on
how the emulator models a 32-bit sub-register write. The state comprises a context object, a
handle slot holding the context pointer, a buffer holding the length dword (absent where a
scenario declares none, which is declared as an empty region), and a stack holding the return
sentinel. The emulator's state is preloaded with the pinned binary's own instruction bytes and has
**no program-memory backing**, so execution can read nothing outside the declared regions.

## What was checked

Expectations were hand-derived from the contract's stated arithmetic and frozen in
`scenarios.json` before any execution code existed (commit `cca093e`; the pinned digest
there was corrected in the following commit, and the validator now anchors the digest on
`reverse_engineering/target.json`). For every scenario the receipt records, and the validator
asserts as **exact expected sequences** rather than permitted sets:

- **Execution** — the ordered PC sequence for its frozen path, every instruction exactly once,
  each with the bytes the emulator decoded and its decoded text, both cross-checked against the
  reviewed listing and (locally) against the pinned binary through `MachOMapper`.
- **Data loads** — the handle `[RDI]` on both paths, the buffer `[RSI]` *only* on the
  `length_from_buffer` path, the saved frame word and the return slot read by the epilogue.
- **Stores** — the eight-byte `PUSH RBP`, the word count, the buffer pointer and the dirty byte,
  in that order, taken from two independent callbacks (P-code `beforeStore` and the execution
  state's own write trace); the two traces agree in every scenario.
- **Return boundary** — the last executed instruction is the original `RET` at `0x15ec3b4`; the
  counter then equals the sentinel read from `[RSP0]`; `RSP` is `RSP0 + 8`; `RBP` is restored to
  the caller's value; `[RSP0-8, RSP0)` holds that value and `[RSP0, RSP0+8)` still holds the
  sentinel. `RAX` is recorded as `ctx` — an observed register outcome, not a declared return type.
  Which registers are asserted, which must still hold their frozen initial value, and which the
  freeze deliberately does not assert are all read from the frozen table, which closes over every
  general-purpose register plus `RFLAGS`; the recorded initial register file is checked register
  by register against the declared initial state.
- **Memory** — the observation region `[ctx+0xc8, ctx+0x129)` with canary bands
  `[ctx+0xb8, ctx+0xc8)` and `[ctx+0x129, ctx+0x139)`: the recorded pre-state equals the state
  the freeze reconstructs byte-for-byte, each declared value landed, no byte outside the declared
  writes changed, and the per-slot pad dwords at `+4` were not written.
- **Result arithmetic** — `13 -> 4`, `0 -> 0`, `0xfffffffc -> 0x3fffffff`,
  `0x7ffffffd -> 0x20000000`, `0x7fffffff -> 0xe0000001`, `-8 -> 0xffffffff`, and for the buffer
  path `0 -> 0`; register outcomes `RAX = ctx`, `RCX = slot*16`, `R9` = the word count, `RDX` per
  the selector path.
- **Fail-closed** — an unsupported userop, a load outside the regions the constructed state
  provides (a missing required input), an uninitialized read the emulator itself reports, an
  exhausted step budget, an exit that is not the original `RET`, or a counter that never reaches
  the sentinel marks the run unqualified with a reason; a run is never partially credited.
- **Determinism** — two independent runs produced byte-identical canonical blocks (SHA-256
  `398d9c49…`). Version metadata, project path and file names live outside the canonical block.

**Useful finding.** In `s05` the word-count store wrote `0x00` as its second byte over a
constructed prefill byte that was already `0x00`. A byte-level before/after diff alone would have
missed that write entirely; the exhaustive store traces record it. This is why the exact store
sequence — not a diff — is the primary evidence, and why the window check asserts containment
while the store traces assert equality.

**Also found.** The emulator's `readUninitialized` callback does not fire for bytes that were
merely never written: a load from an address the constructed state does not provide returns zeros
silently, and the earlier check on that callback was therefore inert. The load-bearing check is on
the load itself — every data-space load must lie inside a region the constructed state declares,
with an absent buffer declared as an empty region — and the callback is kept, with the same arming
boundary, as a defensive hook. The negative-control test removes the buffer from a
`length_from_buffer` scenario and asserts all three outcomes: the required load is recorded as an
uninitialized dependency, the run marks itself unqualified with a reason, and the validator
rejects it. `s13` remains the complementary positive case, where an absent buffer is simply never
read and the run qualifies.

## What this establishes, and what it does not

It establishes the behaviour of the **decoded original instructions inside a Ghidra P-code
emulator** under constructed state: which path executes, which memory is read and written, in
what order, and what the registers hold at the return. The receipt says so itself — it carries
this statement and the frozen limits list verbatim, so the evidence cannot be read as more than
it is — and it is not an independent hardware-level execution of x86 machine code.

It does **not** establish any of the following, and these limits travel with the evidence:

1. The constructed state need not have occurred inside EU IV.
2. Not all valid caller states were explored (`slot ∈ [0, 6)` only; `slot ≥ 6` remains an open
   caller invariant, as does whether callers consume `RAX`).
3. The eventual C++ reconstruction is not validated.
4. No claim is made that `_GfxSetConstantBuffer` is a performance bottleneck.

## Reproducing

The analysed project is the Ghidra project holding an import of the pinned binary; `-noanalysis`
is sufficient, because the pilot preloads the code window from the program's bytes and decodes
through the emulator state.

```sh
# Project location: whatever reverse_engineering.paths resolves (EU4_GHIDRA_WORK, or ~/GhidraEU4)
export EU4_GHIDRA_WORK=~/GhidraEU4
PYTHONPATH=benchmark:. .venv/bin/python -m unittest tests.test_reconstruction_pilot -v
```

The last test re-runs the pilot twice against the analysed project and asserts that the canonical
block reproduces the committed receipt's; another re-runs a scenario with its buffer withheld and
asserts that an unavailable required input fails qualification. The underlying invocation, for a
manual run:

```sh
# The trailing digest is the independently pinned target from reverse_engineering/target.json.
# The script refuses to execute unless the loaded program matches it, its image base and x86-64.
"$GHIDRA_INSTALL/support/analyzeHeadless" <project_dir> EU4_b3d38876 \
  -scriptPath reverse_engineering/ghidra -process eu4 -noanalysis -readOnly \
  -postScript EmulateGfxSetConstantBuffer.java \
  analysis/reconstruction/_GfxSetConstantBuffer/pilot/scenarios.json <output.json> \
  b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d
```

## Provenance

`scenarios.json` — the two ordered paths and every expected value — was committed before the
emulator code existed, so no expectation can have been fitted to an observed run. A mismatch is
to be resolved by amending the contract in a separate commit, never by editing an expectation.

Two declaration defects were corrected rather than papered over, each in a declarations-only
commit whose consumer changed in the commit after it, with the receipt regenerated (its canonical
digest covers the scenario file's bytes, so it changes even when the observed machine-code
behaviour does not):

- the pinned binary digest in `scenarios.json` had a dropped digit; the validator now anchors
  digest checks on `reverse_engineering/target.json`, and the emulator session is refused unless
  the loaded program matches that independently pinned target;
- `initial_registers` described `R9` as starting at zero while the emulator loaded the scenario's
  `r9d` (nonzero on the buffer path). The description was corrected to the zero-extended `r9d`,
  and the declaration is now consumed by the emulator instead of duplicated as a hardcoded
  constant, so the two cannot drift again.

One further, purely explanatory error in that same note is **known and deliberately left in
place for now**: it says the low 32 bits of `R9` are nonzero "for every scenario that does not
take the `length_from_buffer` path", but `s13` takes the explicit path with `r9d = 0`. The
declaration it describes (`R9: r9d_zero_extended`) and everything that consumes it are correct —
the validator resolves the symbol and checks the result against the scenario's own `r9d`,
whatever that value is — and only the sentence is wrong. Correcting one sentence would change the
scenario file's bytes and therefore the receipt's canonical digest, forcing another execution
cycle for wording alone, so the erratum is recorded here and the sentence is corrected at the
next revision that touches the evidence for a substantive reason.

Everything else about the run (constructed addresses, fill pattern, window sizes) is declared in
the same file, and the receipt records what was actually observed; the two are compared in CI by
`tests/test_reconstruction_pilot.py`, which needs neither Ghidra nor the corpus to validate the
committed receipt.
