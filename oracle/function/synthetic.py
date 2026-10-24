"""Deterministic synthetic paired-state producer for A2.

The committed golden is RE0 pilot scenario ``s03``: slot 2, ``R9D = 0xffffffff``,
buffer first dword 13, and the pilot's frozen word count 4. The explicit-path
variant is scenario ``s10``: slot 3, ``R9D = 13``, no buffer region, word count 4.

The producer writes the frozen expectations directly. It does not reimplement the
function's arithmetic; that belongs to RE2/PR C. It constructs only the state the
descriptor declares: the handle pointer, the context observation, both canary bands
and, on the buffer path, the first four buffer bytes. ``raw.synthetic`` is true, so
``confidence="observed"`` cannot be mistaken for runtime-grounded evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

from oracle.function import fixture

CTX = 0x200000000
HANDLE = 0x200010000
BUFFER = 0x300000000

SCENARIOS = {
    "s03": {"slot": 2, "r9d": 0xffffffff, "buffer_dword": 13, "words": 4,
            "buffer_path": True, "sequence": 3,
            "scenario": "s03:length-from-buffer"},
    "s10": {"slot": 3, "r9d": 13, "buffer_dword": None, "words": 4,
            "buffer_path": False, "sequence": 10,
            "scenario": "s10:explicit-length"},
}

GOLDEN_PATH = Path(__file__).resolve().parent / "synthetic" / "golden.txt"


def _fill(start: int, end: int) -> bytes:
    """The reviewed differential memory pattern, by absolute context offset."""
    return bytes(((offset * 55) + 17) & 0xFF for offset in range(start, end))


def _apply_writes(descriptor: dict, scenario: dict, base: bytes) -> bytes:
    layout = descriptor["layout"]
    start = layout["observation_start"]
    post = bytearray(base)
    slot = scenario["slot"]
    word_offset = layout["word_count_base"] + slot * layout["slot_stride"]
    post_offset = layout["pointer_base"] + slot * layout["slot_stride"]
    post[word_offset - start:word_offset - start + 4] = \
        scenario["words"].to_bytes(4, "little")
    post[post_offset - start:post_offset - start + 8] = BUFFER.to_bytes(8, "little")
    post[layout["dirty"] - start] = 1
    return bytes(post)


def _regions(descriptor: dict, scenario: dict) -> dict[str, tuple[int, bytes, bytes]]:
    layout = descriptor["layout"]
    before = _fill(layout["canary_before_start"], layout["canary_before_end"])
    observation = _fill(layout["observation_start"], layout["observation_end"])
    after = _fill(layout["canary_after_start"], layout["canary_after_end"])
    regions = {
        "handle": (HANDLE, CTX.to_bytes(8, "little"), CTX.to_bytes(8, "little")),
        "ctx": (CTX + layout["observation_start"], observation,
                _apply_writes(descriptor, scenario, observation)),
        "canary-before": (CTX + layout["canary_before_start"], before, before),
        "canary-after": (CTX + layout["canary_after_start"], after, after),
    }
    if scenario["buffer_path"]:
        buffer = scenario["buffer_dword"].to_bytes(4, "little")
        regions["buffer"] = (BUFFER, buffer, buffer)
    return regions


def capture_text(descriptor: dict, scenario_name: str = "s03") -> str:
    """Render one deterministic ``FUNCTION-CAPTURE v1`` transcript."""
    scenario = SCENARIOS.get(scenario_name)
    if scenario is None:
        raise fixture.FixtureError(f"unknown synthetic scenario {scenario_name!r}")
    regions = _regions(descriptor, scenario)
    carriers = {
        "RDI": HANDLE,
        "RSI": BUFFER,
        "RCX": scenario["slot"],
        "R9": scenario["r9d"],
    }
    lines = [
        f"{fixture.CAPTURE_HEADER} {fixture.CAPTURE_VERSION}",
        "CAPTURE "
        f"capture=re0-pilot scenario={scenario['scenario']} epoch=synthetic-a2 "
        f"thread=0 sequence={scenario['sequence']} "
        f"function={descriptor['function_id']} "
        f"callsite=unavailable "
        f"module={descriptor['binary']['sha256']} "
        f"policy={fixture.capture_policy_digest(descriptor)} "
        f"fidelity={descriptor['fidelity']['mode']} "
        f"policy_version={descriptor['fidelity']['policy_version']} synthetic=1",
    ]
    for phase, tag in (("pre", "ENTRY"), ("post", "EXIT")):
        for name in fixture.REGION_ORDER:
            if name not in regions:
                continue
            address, pre, post = regions[name]
            data = pre if phase == "pre" else post
            lines.append(f"{tag} name={name} address={hex(address)} bytes={data.hex()}")
    for name, value in carriers.items():
        bits = next(carrier["bits"]
                    for carrier in descriptor["abi"]["required_carriers"]
                    if carrier["name"] == name)
        lines.append(
            f"CARRIER name={name} kind=int bits={bits} value={hex(value)} "
            f"confidence=observed")
    lines.append(f"OUTCOME register=RAX value={hex(CTX)} confidence=observed")
    lines.append(
        f"END regions={2 * len(regions)} carriers={len(carriers)} outcomes=1 losses=0")
    return "\n".join(lines) + "\n"


def main() -> int:
    descriptor = fixture.load_descriptor()
    sys.stdout.write(capture_text(descriptor, "s03"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
