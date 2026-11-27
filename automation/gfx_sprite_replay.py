#!/usr/bin/env python3
"""Offline GPU replay of one isolated sprite draw.

The live engine now reaches and GPU-completes the selected sprite draw. This
module remains the exact isolated pixel qualification: it drives the same
production encoder with the exact original-GL inputs captured for that draw,
seeding the authentic pre-draw so the animated loading-screen background does
not affect the sprite comparison. It is a replay boundary, not a second
renderer.
"""

from __future__ import annotations

import json
import os
import tempfile
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation import (candidate_snapshot, dsh_receipts, gfx_compare, launch_lease,
                        native_build, qualification_sandbox, state)
from automation.gfx_artifacts import sha256_file as _sha

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
HOST_SOURCE = ROOT / "tests" / "gfx_sprite_replay_host.mm"
HOST = BENCH / ".build" / "gfx_sprite_replay_host"
METAL = ROOT / "renderer/metal"
INTEGRATION = ROOT / "renderer/integration"
HEADERS = sorted(header for directory in (BENCH, METAL, INTEGRATION)
                 for header in directory.glob("*.h"))
SOURCES = [
    METAL / "eu4_gfx_shader.mm",
    METAL / "eu4_gfx_buffers.mm",
    METAL / "eu4_gfx_texture.mm",
    INTEGRATION / "eu4_gfx_init.mm",
    BENCH / "eu4_gfx_frame_observe.c",
    METAL / "eu4_gfx_simple_draw.mm",
    METAL / "eu4_gfx_sprite_draw.mm",
    METAL / "eu4_gfx_draw_encode.mm",
    BENCH / "eu4_gfx_logical_capture.mm",
    BENCH / "eu4_metal_events.c",
]


class ReplayError(RuntimeError):
    pass


BUILD_FLAGS = ("-arch", "arm64", "-O2", "-fobjc-arc", "-lc++", "-framework",
               "Foundation", "-framework", "Metal", "-framework", "QuartzCore")


def _compile_host(compiler, sdk, source_root: Path, host: Path, readable) -> None:
    sources = [source_root / path.relative_to(ROOT) for path in (HOST_SOURCE, *SOURCES)]
    result = native_build.qualification_compile(
        compiler, sdk, [*BUILD_FLAGS, "-I", source_root / "benchmark", "-o", host, *sources],
        build=host.parent, readable=readable, timeout=180)
    if result.returncode:
        raise ReplayError((result.stderr or result.stdout)[-1800:] or "sprite replay build failed")


def build_host(force: bool = False) -> Path:
    """Build the historical working-tree host with the maintained compiler recipe."""
    compiler, sdk = native_build.qualification_toolchain("clang")
    members = (HOST_SOURCE, *SOURCES, *HEADERS, Path(__file__))
    identity = {
        "source_digests": {str(path): _sha(path) for path in members},
        "recipe": native_build.qualification_recipe(compiler, sdk, BUILD_FLAGS),
    }
    receipt = HOST.with_suffix(".build.json")
    if (not force and HOST.is_file() and
            dsh_receipts.read_json(receipt) == {**identity, "executable_sha256": _sha(HOST)}):
        return HOST
    HOST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=HOST.parent) as place:
        staged = Path(place) / HOST.name
        _compile_host(compiler, sdk, ROOT, staged, members)
        if identity["source_digests"] != {str(path): _sha(path) for path in members}:
            raise ReplayError("sprite replay sources changed during the working-tree build")
        identity["executable_sha256"] = _sha(staged)
        os.replace(staged, HOST)
    state.atomic_write(receipt, json.dumps(identity, sort_keys=True))
    return HOST


def _execute(host: Path, inputs: Path, output: Path, provenance: tuple[str, str]):
    with launch_lease.gpu_execution():
        completed = qualification_sandbox.run(
            [host, "--reference", inputs, "--out", output], writable=output,
            readable=(inputs,), graphics="metal", timeout=180, original_executable=provenance)
    if completed.returncode:
        raise ReplayError((completed.stderr or completed.stdout)[-1800:] or "sprite replay execution failed")
    return completed


def run_replay(reference_dir: Path, output: Path, *, force_build: bool = False) -> dict:
    reference_dir = Path(reference_dir).resolve()
    descriptor_path = reference_dir / "gl-reference.json"
    if not descriptor_path.is_file():
        raise ReplayError(f"reference descriptor is absent: {descriptor_path}")
    reference = json.loads(descriptor_path.read_text())
    provenance = reference.get("provenance") or {}
    executable = provenance.get("executable")
    executable_sha = provenance.get("executable_sha256")
    if not executable or not executable_sha:
        raise ReplayError("reference does not declare executable provenance")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    host = build_host(force=force_build)
    ran = _execute(host, reference_dir, output, (str(executable), str(executable_sha)))
    candidate_path = output / "metal-logical.json"
    if ran.returncode != 0 or not candidate_path.is_file():
        raise ReplayError(
            (ran.stderr or ran.stdout or "M5 replay host failed").strip()
        )
    candidate = json.loads(candidate_path.read_text())
    capture = candidate.get("capture") or {}
    raw = output / str(capture.get("raw_file") or "")
    if not raw.is_file():
        raise ReplayError("M5 replay did not write its raw payload")
    if raw.stat().st_size != capture.get("raw_bytes") or _sha(raw) != capture.get("raw_sha256"):
        raise ReplayError("M5 replay raw payload does not match its declaration")
    if (candidate.get("gpu") or {}).get("completed") is not True:
        raise ReplayError("M5 replay GPU command did not complete")
    return {
        "candidate": candidate,
        "candidate_path": str(candidate_path),
        "raw_path": str(raw),
        "host": str(host),
        "stdout": ran.stdout.strip(),
    }


def qualify_candidate(commit: str, directory: Path, original: Path, reference: dict,
                      configuration: dict) -> dict:
    """Run the production sprite encoder with the four protected pre-draw inputs."""
    import platform

    manifest_path = ROOT / configuration["evidence_manifest"]
    if _sha(manifest_path) != reference["manifest_sha256"]:
        raise ReplayError("sprite evidence manifest identity differs")
    evidence = json.loads(manifest_path.read_text())
    spec = evidence["reference"]
    descriptor = original / spec["descriptor"]
    if _sha(descriptor) != spec["descriptor_sha256"]:
        raise ReplayError("original sprite descriptor identity differs")
    inputs = directory / "inputs"
    inputs.mkdir()
    for key in ("vertex", "constant", "texture", "pre_draw"):
        item = spec[key]
        source = original / item["file"]
        contents = source.read_bytes()
        if len(contents) != item["bytes"] or _sha(source) != item["sha256"]:
            raise ReplayError(f"sprite input {key} differs from its pinned identity")
        if item["sha256"] == spec["raw"]["sha256"]:
            raise ReplayError("sprite pre-draw input duplicates the protected output")
        (inputs / source.name).write_bytes(contents)
    original_capture = gfx_compare.from_gl_reference(descriptor)
    policy_path = ROOT / configuration["comparison_policy"]
    if _sha(policy_path) != evidence["policy"]["sha256"]:
        raise ReplayError("sprite comparison policy differs from its pinned identity")

    snapshot = directory / "sources"
    relative = [path.relative_to(ROOT).as_posix() for path in SOURCES]
    paths = sorted(set(relative) | {
        path.relative_to(ROOT).as_posix() for path in HEADERS if path.is_file()})
    closure = candidate_snapshot.materialize_commit(ROOT, commit, snapshot, tuple(paths))
    harness_name = HOST_SOURCE.relative_to(ROOT).as_posix()
    candidate_snapshot.stage_trusted_sources(ROOT, snapshot, (harness_name,), closure)
    compiler, sdk = native_build.qualification_toolchain("clang")
    build = directory / "build"
    build.mkdir()
    executable = build / "sprite-replay"
    _compile_host(compiler, sdk, snapshot, executable, (snapshot,))
    expected_exe = json.loads(descriptor.read_text())["provenance"]
    executable_digest = _sha(executable)
    output = directory / "metal"
    output.mkdir()
    (output / "events").mkdir()
    _execute(executable, inputs, output,
             (expected_exe["executable"], evidence["executable_sha256"]))
    required = {"metal-logical.json", "metal-logical.bgra"}
    allowed = required | {"events/create.json", "events/init.json", "events/shader-set-001.json"}
    if _sha(executable) != executable_digest:
        raise ReplayError("sprite replay executable changed")
    outputs = dsh_receipts.worker_outputs(output, allowed, required=required, base=directory)
    result_path = output / "metal-logical.json"
    if json.loads(result_path.read_text())["capture"]["raw_file"] != "metal-logical.bgra":
        raise ReplayError("sprite readback file identity differs")
    report = gfx_compare.compare(original_capture, gfx_compare.from_metal_logical(result_path),
                                 gfx_compare.load_policy(policy_path))
    exact = report["status"] == "pass" and report["samples"]["mismatched_pixels"] == 0
    return {"verdict": "exact" if exact else "different", "evidence_tier": "original-eu4",
            "execution": "completed", "independently_verified": True,
            "reference": reference, "coverage": ["sprite-logical-bgra"], "scope": report["samples"],
            "candidate": {"source_digests": closure, "executable_sha256": executable_digest,
                          "architecture": "arm64"},
            "environment": {"macos": platform.mac_ver()[0], "os_build": platform.version()},
            "recipe": native_build.qualification_recipe(compiler, sdk, BUILD_FLAGS),
            "validation_contract": {"comparison_policy_sha256": _sha(policy_path)},
            "outputs": outputs,
            "problems": [] if exact else report["reasons"]}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force-build", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_replay(
            Path(args.reference), Path(args.output), force_build=args.force_build
        )
    except (ReplayError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1
    print(json.dumps({
        "status": "ok",
        "candidate": result["candidate_path"],
        "raw": result["raw_path"],
        "host": result["host"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
