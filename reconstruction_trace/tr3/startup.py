"""Load the one-shot dylib into a controlled x86-64 host.

The expected file and window digests are computed here and passed in. The
dylib only compares them. This module does not launch EU4.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from reverse_engineering.corpus import sha256_file
from reverse_engineering.macho_rva import MachOMapper

from reconstruction_trace.tr3 import image, native

HERE = Path(__file__).resolve().parent
SOURCES = ("host.c", "subject.S", "insert.c", "insert_stub.S", "startup.py", "image.py", "path.py")
CLAIMS = {
    "controlled_host_startup_loading": "demonstrated",
    "one_shot_qbdi_interception": "demonstrated",
    "eu4_startup_loading": "not demonstrated",
    "eu4_hook_reachability": "not demonstrated",
    "eu4_memory_provenance": "not demonstrated",
    "live_gfx_set_constant_buffer": "not authorized",
}


def _symbol_rva(path: Path, name: str) -> int:
    preferred = image.text_vmaddr(path)
    listed = subprocess.run(["nm", "-arch", "x86_64", str(path)], capture_output=True, text=True,
                            check=True)
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == name:
            return int(parts[0], 16) - preferred
    raise RuntimeError(f"{name} is not in {path}")


def build(build_dir: Path) -> tuple[Path, Path]:
    prefix = native.qbdi_prefix()
    build_dir.mkdir(parents=True, exist_ok=True)
    dylib = build_dir / "libtr3insert.dylib"
    host = build_dir / "tr3_host"
    subprocess.run(
        ["clang", *native.CFLAGS, "-dynamiclib", "-I", str(prefix / "include"), "-o", str(dylib),
         str(HERE / "insert.c"), str(HERE / "insert_stub.S"),
         str(prefix / "lib" / "libQBDI.a"), "-lc++"],
        check=True)
    subprocess.run(
        ["clang", *native.CFLAGS, "-o", str(host), str(HERE / "host.c"), str(HERE / "subject.S")],
        check=True)
    return host, dylib


def capture(build_dir: Path) -> tuple[dict, dict]:
    """Run the host once. A rel32 miss raises; it does not switch mechanisms."""
    reason = native.unsupported_host_reason()
    if reason:
        raise RuntimeError(reason)
    host, dylib = build(build_dir)
    preferred = image.text_vmaddr(host)
    entry_rva = _symbol_rva(host, "_tr3_subject")
    window_end = entry_rva + native.SUBJECT_BYTES
    with MachOMapper(host) as mapper:
        window = mapper.read_bytes_at_rva(preferred, entry_rva, native.SUBJECT_BYTES)
    file_hash = sha256_file(host)
    window_hash = hashlib.sha256(window).hexdigest()
    log_path = build_dir / "insert.json"
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(dylib)
    env["TR3_EXPECT_FILE_SHA256"] = file_hash
    env["TR3_EXPECT_WINDOW_SHA256"] = window_hash
    env["TR3_ENTRY_RVA"] = hex(entry_rva)
    env["TR3_WINDOW_LEN"] = str(native.SUBJECT_BYTES)
    env["TR3_LOG"] = str(log_path)
    # `arch -x86_64` drops DYLD_INSERT_LIBRARIES. The host is already x86-64,
    # so executing it directly is what loads the dylib under Rosetta.
    proc = subprocess.run([str(host)], capture_output=True, text=True, env=env, check=False)
    if not log_path.is_file():
        raise RuntimeError(f"dylib wrote no log: {proc.returncode} {proc.stderr.strip()}")
    raw = json.loads(log_path.read_text())
    host_out = json.loads(proc.stdout)
    if proc.returncode != 0 or raw.get("refusal"):
        raise RuntimeError(
            f"startup interception failed ({raw.get('refusal')}): {proc.stderr.strip()}")
    if raw["proc_translated"] != 1:
        raise RuntimeError(f"sysctl.proc_translated is {raw['proc_translated']}, not 1")
    hook = raw["hook"]
    if (not raw["patched"] or hook["count"] != 1 or hook["qbdi_invocations"] != 1
            or host_out["calls"] != 2 or not hook["restored_before_qbdi"]
            or not hook["entry_bytes_match_after"] or host_out["entry"] != hook["saved_entry"]):
        raise RuntimeError(f"one-shot startup interception was not demonstrated: {hook}")
    loaded = image.runtime_site(int(raw["image"]["preferred_base"], 16),
                                int(raw["image"]["slide"], 16),
                                int(raw["image"]["entry_rva"], 16),
                                int(raw["image"]["header"], 16))
    if loaded != int(raw["image"]["runtime_site"], 16):
        raise RuntimeError("runtime site does not match preferred base plus slide plus entry")
    gap = native.subject_gap(raw)
    if gap:
        raise RuntimeError(gap)
    observation = native.observation_from_run(raw["run"], raw["qbdi_version"])
    native.schema.validate(observation)
    receipt = {
        "schema": "tr3-startup-receipt/v1",
        "work_order": "TR3",
        "evidence": native.EVIDENCE,
        "function_id": native.FUNCTION_ID,
        "binary": None,
        "claims": dict(CLAIMS),
        "concurrency": "single-threaded host; another thread entering while the hook is removed is untraced",
        "host_result": host_out,
        "image": raw["image"],
        "hook": {**hook, "native_call_count": host_out["calls"]},
        "expected_file_sha256": file_hash,
        "expected_window_sha256": window_hash,
        "window": {"image_base": preferred, "entry_rva": entry_rva, "end_rva": window_end},
        "proc_translated": raw["proc_translated"],
        "qbdi_version": raw["qbdi_version"],
        "probe": {"sources_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                     for name in SOURCES}},
        "probe_output": raw,
    }
    return observation, receipt


def write_capture(out_dir: Path, build_dir: Path) -> dict:
    observation, receipt = capture(build_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    observation_path = out_dir / "startup_observation.json"
    observation_path.write_text(json.dumps(observation, indent=2) + "\n")
    receipt["observation_sha256"] = hashlib.sha256(observation_path.read_bytes()).hexdigest()
    (out_dir / "startup_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    import sys
    reason = native.unsupported_host_reason()
    if reason:
        print(f"TR3 startup host unsupported: {reason}", file=sys.stderr)
        return 2
    receipt = write_capture(
        HERE, Path.home() / ".cache" / "eu4-mac-perf" / "tr3-startup")
    print(receipt["claims"]["one_shot_qbdi_interception"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
