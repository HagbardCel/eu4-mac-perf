"""Load the one-shot dylib into a controlled x86-64 host.

The expected file and window digests are computed here and passed in. The
dylib only compares them. This module does not launch EU4.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

from reverse_engineering.corpus import sha256_file
from reverse_engineering.macho_rva import MachOMapper

from reconstruction_trace import fixture
from reconstruction_trace.tr3 import adapter, image, native

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
GPR_IMAGE_BYTES = 18 * 8
FXSAVE_IMAGE_BYTES = 512
CEILING = {"max_instructions": 64, "max_memory_accesses": 256, "max_bytes": 65536}
_BUDGET_ENV = (
    ("max_instructions", "TR3_MAX_INSTRUCTIONS"),
    ("max_memory_accesses", "TR3_MAX_ACCESSES"),
    ("max_bytes", "TR3_MAX_BYTES"),
)


def envelope(window_len: int) -> int:
    """Entry and exit GPR, both FXSAVE images, and the mapped window."""
    return 2 * GPR_IMAGE_BYTES + 2 * FXSAVE_IMAGE_BYTES + window_len


def _hex_addr(text: object) -> int:
    if not isinstance(text, str) or not text.startswith("0x"):
        raise RuntimeError("address is not hex")
    body = text[2:]
    if body != "0" and (body == "" or body[0] == "0" or any(c not in "0123456789abcdef" for c in body)):
        raise RuntimeError("address is not canonical hex")
    return int(text, 16)


def check_descriptor(desc: dict, preferred: int) -> None:
    """Refuse a trace-target/v1 shape the engine is not allowed to run."""
    if desc.get("schema") != "trace-target/v1":
        raise RuntimeError("schema is not trace-target/v1")
    if desc.get("trigger") != {"mode": "first-invocation"}:
        raise RuntimeError("trigger is not first-invocation")
    binary, function = desc["binary"], desc["function"]
    ranges = function["ranges"]
    if not isinstance(ranges, list) or len(ranges) != 1 or len(ranges[0]) != 2:
        raise RuntimeError("function range is not one pair")
    entry = _hex_addr(function["entry_rva"])
    start, end = _hex_addr(ranges[0][0]), _hex_addr(ranges[0][1])
    if start != entry or end <= start:
        raise RuntimeError("range does not start at the entry")
    if _hex_addr(binary["image_base"]) != preferred:
        raise RuntimeError("image base is not the preferred base")
    for digest in (binary["sha256"], binary["window_sha256"]):
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise RuntimeError("digest is not sha256")
    for key in ("max_instructions", "max_memory_accesses", "max_bytes"):
        value = desc["capture"][key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError(f"{key} is not a positive integer")


def prepare_target(host: Path, symbol: str, budgets: dict) -> dict:
    preferred = image.text_vmaddr(host)
    entry = _symbol_rva(host, symbol)
    length = native.SUBJECT_BYTES if symbol == "_tr3_subject" else _symbol_rva(host, symbol + "_end") - entry
    with MachOMapper(host) as mapper:
        window = mapper.read_bytes_at_rva(preferred, entry, length)
    prepared = {
        "entry_rva": entry,
        "window_len": length,
        "window": window,
        "file_hash": sha256_file(host),
        "window_hash": hashlib.sha256(window).hexdigest(),
        "symbol": symbol[1:] if symbol.startswith("_") else symbol,
        "preferred": preferred,
    }
    prepared["descriptor"] = {
        "schema": "trace-target/v1",
        "binary": {
            "sha256": prepared["file_hash"],
            "image_base": hex(preferred),
            "window_sha256": prepared["window_hash"],
        },
        "function": {
            "name": prepared["symbol"],
            "entry_rva": hex(entry),
            "ranges": [[hex(entry), hex(entry + length)]],
        },
        "trigger": {"mode": "first-invocation"},
        "capture": {
            "max_instructions": budgets["max_instructions"],
            "max_memory_accesses": budgets["max_memory_accesses"],
            "max_bytes": budgets["max_bytes"],
        },
    }
    check_descriptor(prepared["descriptor"], preferred)
    return prepared


def target_env(dylib: Path, log: Path, descriptor: dict, *, preferred: int, legacy: bool = False) -> dict:
    """Translate one validated descriptor. The dylib still applies the loaded slide itself."""
    check_descriptor(descriptor, preferred)
    function = descriptor["function"]
    binary = descriptor["binary"]
    entry = _hex_addr(function["entry_rva"])
    end = _hex_addr(function["ranges"][0][1])
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(dylib)
    env["TR3_EXPECT_FILE_SHA256"] = binary["sha256"]
    env["TR3_EXPECT_WINDOW_SHA256"] = binary["window_sha256"]
    env["TR3_ENTRY_RVA"] = function["entry_rva"]
    env["TR3_WINDOW_LEN"] = str(end - entry)
    env["TR3_CHECK_SYMBOL"] = "1"
    env["TR3_SYMBOL"] = function["name"]
    env["TR3_LOG"] = str(log)
    env.pop("TR3_LEGACY_REGIONS", None)
    if legacy:
        env["TR3_LEGACY_REGIONS"] = "1"
    for key, name in _BUDGET_ENV:
        env.pop(name, None)
        env[name] = str(descriptor["capture"][key])
    return env


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


def acquire(host: Path, env: dict, log_path: Path, runner=None):
    """Launch once and read the log this launch wrote. A missing log is not a previous run."""
    log_path.unlink(missing_ok=True)
    run = subprocess.run if runner is None else runner
    proc = run([str(host)], capture_output=True, text=True, env=env, check=False)
    if not log_path.is_file():
        raise RuntimeError(f"dylib wrote no log: {proc.returncode} {(proc.stderr or '').strip()}")
    return proc, json.loads(log_path.read_text())


def accept(proc: subprocess.CompletedProcess, raw: dict, file_hash: str, window_hash: str):
    """Require a successful interception. This is not the path for an expected refusal."""
    for key, expected in (("expected_file_sha256", file_hash), ("file_sha256", file_hash),
                          ("expected_window_sha256", window_hash), ("window_sha256", window_hash)):
        if raw.get(key) != expected:
            raise RuntimeError(f"{key} does not match this executable")
    host_out = json.loads(proc.stdout)
    if proc.returncode != 0 or raw.get("refusal"):
        raise RuntimeError(
            f"startup interception failed ({raw.get('refusal')}): {(proc.stderr or '').strip()}")
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
    if observation["completeness"] != "complete":
        raise RuntimeError("incomplete capture: " + "; ".join(observation["incomplete_reasons"]))
    qualified = fixture.qualify(observation)
    if not qualified["fixture_qualified"]:
        raise RuntimeError("unqualified capture: " + "; ".join(qualified["reasons"]))
    if adapter.prepare_scenario(observation)["refusal"] != "unauthenticated-target":
        raise RuntimeError("unexpected adapter result")
    return observation, host_out


def launch(build_dir: Path, *, file_hash: str | None = None, window_hash: str | None = None,
           runner=None):
    """Build the host and acquire its log. The caller decides success or refusal."""
    reason = native.unsupported_host_reason()
    if reason:
        raise RuntimeError(reason)
    host, dylib = build(build_dir)
    prepared = prepare_target(host, "_tr3_subject", CEILING)
    built = {
        "file_hash": prepared["file_hash"], "window_hash": prepared["window_hash"],
        "entry": prepared["window"][:5].hex(), "preferred": prepared["preferred"],
        "entry_rva": prepared["entry_rva"],
        "window_end": prepared["entry_rva"] + prepared["window_len"],
    }
    descriptor = copy.deepcopy(prepared["descriptor"])
    if file_hash is not None:
        descriptor["binary"]["sha256"] = file_hash
    if window_hash is not None:
        descriptor["binary"]["window_sha256"] = window_hash
    log = build_dir / "insert.json"
    env = target_env(dylib, log, descriptor, preferred=prepared["preferred"], legacy=True)
    # `arch -x86_64` drops DYLD_INSERT_LIBRARIES. The host is already x86-64,
    # so executing it directly is what loads the dylib under Rosetta.
    proc, raw = acquire(host, env, log, runner)
    return proc, raw, built


def capture(build_dir: Path) -> tuple[dict, dict]:
    """Run the host once. A rel32 miss raises; it does not switch mechanisms."""
    proc, raw, built = launch(build_dir)
    observation, host_out = accept(proc, raw, built["file_hash"], built["window_hash"])
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
        "hook": {**raw["hook"], "native_call_count": host_out["calls"]},
        "expected_file_sha256": built["file_hash"],
        "expected_window_sha256": built["window_hash"],
        "window": {"image_base": built["preferred"], "entry_rva": built["entry_rva"],
                   "end_rva": built["window_end"]},
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
