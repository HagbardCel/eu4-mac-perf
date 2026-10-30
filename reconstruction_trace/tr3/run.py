"""Launch entry for one `_GfxSetConstantBuffer` trace.

Verifies the pin recorded in read-only ``reverse_engineering/target.json``,
then refuses. Operator authorization is absent, so this process does not
install instrumentation or attach. ``image_base`` is the preferred image base,
not the process load address. Tests and CI do not call this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from reconstruction_trace.tr3 import image

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "reverse_engineering" / "target.json"

PIN_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PIN_ENTRY_RVA = "0x15ec36a"
PIN_END_RVA = "0x15ec3b5"
PIN_WINDOW_SHA256 = "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"


def decide(target: dict) -> str:
    """Refuse a pin mismatch, and otherwise refuse because the run is unauthorized."""
    window = target["code_windows"]["_GfxSetConstantBuffer"]
    pinned = (target["sha256"] == PIN_SHA256
              and window["entry_rva"] == PIN_ENTRY_RVA
              and window["end_rva"] == PIN_END_RVA
              and window["sha256"] == PIN_WINDOW_SHA256)
    if not pinned:
        return "refused: binary pin mismatch"
    return "refused: operator authorization is absent"


def main() -> int:
    target = json.loads(TARGET.read_text())
    decision = decide(target)
    if decision != "refused: operator authorization is absent":
        print(decision)
        return 1
    window = target["code_windows"]["_GfxSetConstantBuffer"]
    path = Path(target["default_executable"])
    if not path.is_file():
        print("refused: executable missing")
        return 1
    reason = image.check(path, {
        "file_sha256": target["sha256"],
        "window_sha256": window["sha256"],
        "image_base": target["image_base"],
        "entry_rva": int(window["entry_rva"], 16),
        "end_rva": int(window["end_rva"], 16),
    })
    if reason:
        print(f"refused: {reason}")
        return 1
    print(decision)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
