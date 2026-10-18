# RE1 — faithful reconstruction of `_GfxSetConstantBuffer`

Handwritten C++20 functional reimplementation of the function RE0 characterised
([`../contract.md`](../contract.md), [`../instructions.json`](../instructions.json),
[`../pilot/pilot.md`](../pilot/pilot.md)). It is written from the documented contract — offsets,
widths, the two arithmetic paths, the three writes — not from decompiler output, under the narrow
exception recorded in [`docs/conventions.md`](../../../../docs/conventions.md).

Nothing here launches, attaches to or reads the game. A differential comparison against the
original's executed instructions is RE2, not this step.

## Files

| File | Role |
| --- | --- |
| `gfx_set_constant_buffer.hpp` | Object layout with `static_assert`s on the contract's offsets, the two arithmetic paths, and the entry declarations |
| `gfx_set_constant_buffer.cpp` | The store sequence (`words`, `buffer`, `dirty`, in that order) and the six-position ABI adapter |
| `harness.cpp` | Dumb stdin/stdout driver: `words` (arithmetic sweep), `scenario` (apply the function to a supplied context), `exhaustive` (opt-in whole-domain sweep) |
| `arith_literals.cpp` | The two paths written a second time, instruction by instruction, in their own translation unit — see *The exhaustive sweep* |
| `abi_check.cpp`, `abi_call6.S` | x86-64 ABI probe: calls the adapter with the original's argument registers |
| `../../../../tests/test_reconstruction_faithful.py` | The RE1 tests |

## ABI

The original reads `RDI` (handle), `RSI` (buffer), `RCX` (slot) and `R9D` (selector); it never
reads `RDX` or `R8`. A four-argument C++ function would pass `slot` in `RDX` and `selector` in
`RCX`, so the reconstruction exposes a six-position adapter
(`gfx_set_constant_buffer_abi`) that matches the original's call shape, with two placeholder
positions:

| Position | Register | Meaning |
| --- | --- | --- |
| 1 | `RDI` | context handle (`ctx = *handle`) |
| 2 | `RSI` | constant-buffer pointer (stored; its first dword is the byte length when the selector is `-1`) |
| 3 | `RDX` | **unused** placeholder |
| 4 | `RCX` | slot index |
| 5 | `R8` | **unused** placeholder |
| 6 | `R9D` | length selector |

`void *` is a provisional return carrier because the original leaves the context in `RAX`. That
does **not** establish the original's return type: whether any of the 37 callers consume it is
still an open caller-liveness question, and the contract treats `RAX` as a recorded outcome.

For ordinary use, `gfx_set_constant_buffer` takes only the four meaningful inputs and the adapter
forwards to it. Every `scenario` run in the harness goes through the adapter with garbage in the
two placeholders, so the unused positions are re-checked on every run.

Post-call values in caller-saved registers other than `RAX` are **diagnostic evidence only**:
ordinary compiled C++ does not promise them, so a differential comparison must not treat a
difference in `RDX`, `RCX` or `R9` as a failure.

The x86-64 probe (`abi_check.cpp` + `abi_call6.S`) loads the six registers directly and calls the
adapter, asserting that the slot came from `RCX`, the selector from `R9D`, the buffer from `RSI`,
the dirty write happened and `RAX` came back as the context — and that a value sitting in `RDX` or
`R8` is not consumed. It is built only where an x86-64 binary can be assembled and executed (on
x86-64 hosts directly, on Apple Silicon through `-arch x86_64` and Rosetta); elsewhere the test
skips and says so.

That a wrong mapping would not pass silently follows from the probe's structure — `RDI` must hold
the handle, so an unshifted call cannot work at all, and the position-sensitivity checks put
in-domain values in the *wrong* registers, where they fail a check rather than crash. Locally it
was also checked empirically: swapping the `RCX`/`RDX` moves in the trampoline produces no
`abi=ok` line at all (the mis-placed slot reaches an out-of-domain index). That local experiment
is not a committed test.

## Arithmetic

Both paths are stated at the instruction level in the header. The explicit-length path wraps in
32 bits *before* the sign test, so the C++ converts the wrapped sum to a signed value — defined
modulo 2^32 in C++20, unlike an overflowing signed addition — and divides with `/`:

    words = (int32_t)(selector + 3) / 4            // LEA/TEST/CMOVNS/SAR

The buffer path uses the logical shift the instruction uses:

    words = (first_dword + 3) >> 2                 // ADD/SHR, unsigned

The near misses these tests discriminate against are `selector / 4` (no `+ 3` wrap) and a signed
division on the buffer path. They differ only on some inputs, so the boundary corpus is checked
to *contain* discriminating cases (for example `-7` is not one: the wrapped sum is `-4`, and both
readings give `-1`).

## Tests

`tests/test_reconstruction_faithful.py` always checks the instruction model and the documented
proof of the division identity against RE0's frozen expectations, and — when a C++ compiler is
available — builds the reconstruction and:

* drives both paths over the boundary corpus and a deterministic 4096-value sample and compares
  every result with the instruction model (not with the C++ expression restated);
* replays all 13 frozen pilot scenarios through the compiled adapter, asserting the word count,
  the buffer role, the dirty byte, the returned `RAX` role, and that no byte outside the three
  declared fields changed;
* checks the refusals: a slot outside `[0, 6)`, a buffer-reading selector with no buffer contents,
  a mis-sized context. All of these exit non-zero with an `error=` line rather than proceeding.

The default host build uses `-O0 -g -Wall -Wextra -Werror -fsanitize=address,undefined`; the test
falls back to a plain build only if the sanitized build or binary cannot run here, and reports
which one it used.

### The exhaustive sweep

`EU4_RE1_EXHAUSTIVE=1` adds an opt-in sweep of all 2^32 inputs of both paths. It is not a CI gate:
the documented proof plus the corpus and sample cover the requirement, and this only closes the
domain mechanically. The sweep compares the reconstruction against the instruction encodings in
`arith_literals.cpp`, a separate translation unit built without LTO, so the compiler cannot prove
the two equal and fold the comparison (and the loop) away; a volatile sink keeps the loop
observable. The summed values are additionally checked against the closed forms derived in the
test (a whole-domain modular sum, hence order- and platform-independent).

## Verification record

**Local (2026-10-18),** Apple Silicon macOS with `clang++`: 16 tests, none failed, one skipped by
default (the exhaustive sweep; run separately with `EU4_RE1_EXHAUSTIVE=1` and passed, digest
`6000000360000000`). The x86-64 ABI probe built as Mach-O `x86_64` and executed under Rosetta:
all ten checks `ok`, `abi=ok`. The sanitized host build was used.

In CI the tests run on Linux with whatever C++20 compiler is present; if there is none, they
skip and report the reason rather than passing silently.

## Limits

* `slot` is only in `[0, 6)`. The original checks nothing; outside that range a `slots[slot]`
  access would be undefined behaviour, so the harness refuses those inputs. The array's real
  length and the aliasing at `slot >= 6` remain open (see the contract's *Slot range*).
* The context object here is `0x130` bytes (the prefix through the dirty byte plus alignment);
  RE0's emulator constructs `0x140`. The three writes are inside both, and only the shared range
  is meaningful.
* The arithmetic, not the code generation. This does not reproduce the original's instruction
  bytes, register traffic or stack frame, and does not claim to — `-O0` code is not even a leaf.
* No hardware-level claim: RE0's reference is a P-code emulator, so RE2 will compare a
  reconstruction with an emulated original, not with the machine code running on a CPU.
* No GL, no game state, no performance claim. This function's three memory writes are all that is
  modelled.

## Next

RE2: a frozen differential corpus in the same domain, the original's P-code execution as the
reference, and a role-normalised comparison of the reconstruction against it — including the
negative implementations the arithmetic here is only tested against analytically. That corpus will
freeze *inputs* rather than expected outputs, so it needs its own qualification mode and must not
inherit the pilot receipt validator's `expected_words`-style requirements.
