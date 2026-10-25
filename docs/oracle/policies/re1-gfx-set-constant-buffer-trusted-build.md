# RE1 trusted build record — `re1-gfx-set-constant-buffer-acceptance`

This note documents the human-reviewed pin for
[`re1-gfx-set-constant-buffer-trusted-build.json`](re1-gfx-set-constant-buffer-trusted-build.json).
E1 loads that JSON at a fixed path during candidate binding; it does not compile RE1
during certification.

## Approved artifact

| Field | Value |
| --- | --- |
| `build_identity` | `re1-gfx-set-constant-buffer-acceptance` |
| `source_sha256` (four files) | `ea5b50c049e8596365f65e1407e903ae12be98a50aca539e1adeb19bab31ad75` |
| `executable_sha256` | `61a20732d7ee91375567de9d9f764d769829bc196ad02e1edabc050739bb0560` |
| Platform | **darwin / arm64** (macOS Apple silicon) |

Source inventory (sorted digest inputs): `arith_literals.cpp`,
`gfx_set_constant_buffer.cpp`, `gfx_set_constant_buffer.hpp`, `harness.cpp` under
`analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/`.

## Build procedure (operator, one-time)

1. Checkout the RE1 reconstruction tree at the commit recorded in PR **#86**.
2. On **darwin arm64**, with AddressSanitizer and UBSan available to the toolchain,
   compile the four sources with exactly the `build_flags` in the JSON record.
3. Record `sha256` of the resulting executable as `executable_sha256`.
4. Copy the approved bytes to E2 fixture
   `tests/oracle_certify_acceptance/fixtures/re1-approved-harness` on branch
   `feature/oracle-e2-audit` and verify the file digest matches the record.

Example command (flags must match the record):

```text
c++ -std=c++20 -O0 -g -Wall -Wextra -Werror \
  -fsanitize=address,undefined -fno-sanitize-recover=all \
  gfx_set_constant_buffer.cpp harness.cpp arith_literals.cpp \
  -o re1-approved-harness
```

## Approval

| Item | Value |
| --- | --- |
| PR | [#86](https://github.com/HagbardCel/eu4-mac-perf/pull/86) (E1 certifier remediation rev 4) |
| E2 fixture alignment | `feature/oracle-e2-audit` @ `f714883` (`re1-approved-harness.meta.json`) |
| Review date | 2026-10-25 |

## Host compatibility

**Production-binding** acceptance tests (E2 gates 2–3) copy the approved fixture and
call `certify_session` with the **unpatched** production record. They run only on
hosts compatible with the pinned platform (darwin arm64 + sanitizer runtime).

**Portable** CI on Linux uses a **patched** `load_production_build_record` and a
locally built harness; that path exercises binding logic without executing the
macOS-approved binary.

Never refresh the JSON or fixture from CI heuristics or `certify_session()` output.
Digest changes require an explicit reviewed update to this note, the JSON record,
and the E2 fixture bytes.
