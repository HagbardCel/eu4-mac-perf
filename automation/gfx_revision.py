"""Operator-owned policy and receipts for offline candidate qualification."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

from automation import candidate_snapshot, dsh_receipts, qualification_sandbox, state
from automation.gfx_artifacts import sha256_bytes, sha256_file

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "eu4-revision-qualification/v1"
POLICY = "EU4_QUALIFICATION_POLICY"
POLICY_DIGEST = "EU4_QUALIFICATION_POLICY_SHA256"
FUNCTION_SOURCES = ("renderer/engine/gfx_set_constant_buffer.cpp",
                    "renderer/engine/gfx_set_constant_buffer.hpp")


class QualificationError(RuntimeError):
    pass


def _outside(path: Path, workspace: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(workspace) or workspace.is_relative_to(resolved):
        raise QualificationError(f"path overlaps candidate workspace: {resolved}")
    return resolved


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "--no-replace-objects", "-c", "core.fsmonitor=false",
         "-C", str(ROOT), *args],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_NOSYSTEM": "1"}, text=True, timeout=60).strip()


def _policy(workspace: Path, commit: str) -> tuple[dict, str]:
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise QualificationError("a full candidate commit is required")
    if not (path := os.environ.get(POLICY)):
        raise QualificationError("operator qualification policy is unconfigured")
    raw = _outside(Path(path), workspace).read_bytes()
    digest = sha256_bytes(raw)
    if digest != os.environ.get(POLICY_DIGEST):
        raise QualificationError("operator qualification policy digest differs")
    policy = json.loads(raw)
    if not isinstance(policy, dict) or policy.get("schema") != SCHEMA:
        raise QualificationError("unsupported qualification policy")
    _outside(ROOT, workspace)
    trusted = _git("rev-parse", "HEAD")
    if trusted != policy.get("trusted_revision") or _git(
            "status", "--porcelain", "--untracked-files=all"):
        raise QualificationError("trusted validator checkout differs from its approved revision")
    state.require_worktree(workspace, ROOT, commit)
    return policy, digest


def _xcrun(*args: str) -> Path:
    return Path(subprocess.check_output(
        ["/usr/bin/xcrun", *args], env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        text=True, timeout=60).strip())


def _applicable_execution(receipt_path: Path, binding: dict) -> dict | None:
    """Verify a protected receipt and its outputs without relabeling it."""
    try:
        if (receipt_path.is_symlink() or not receipt_path.is_file()
                or receipt_path.stat().st_nlink != 1):
            return None
        previous = dsh_receipts.read_json(receipt_path)
        if (not isinstance(previous, dict) or previous.get("schema") != SCHEMA
                or previous.get("execution") != "completed"
                or dsh_receipts.verify_receipt_digest(previous)
                or any(previous.get(key) != value for key, value in binding.items())):
            return None
        outputs = previous.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            return None
        for item in outputs:
            name = item["file"]
            if not name or Path(name).name != name or name in (".", ".."):
                return None
            path = receipt_path.parent / name
            if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
                    or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]):
                return None
        return previous
    except (KeyError, TypeError, ValueError, OSError):
        return None


def qualify_function(commit: str, directory: Path, reference_identity: dict, *,
                     previous: Path | None = None) -> dict:
    from reverse_engineering import reconstruction_diff as diff

    candidate = directory / "candidate"
    closure = candidate_snapshot.materialize_commit(ROOT, commit, candidate, FUNCTION_SOURCES)
    reference = diff.load_reference()
    corpus = diff.load_corpus()
    references = {
        "corpus_sha256": diff.corpus_sha256(),
        "reference_sha256": sha256_file(diff.FUNCTION_DIR / "differential/reference.json"),
    }
    if reference_identity != references:
        return {"verdict": "invalid_provenance", "problems": ["function reference identity differs"]}
    if problems := diff.validate_reference(reference, corpus):
        return {"verdict": "invalid_provenance", "problems": problems}

    compiler = _xcrun("--find", "clang++")
    sdk = _xcrun("--sdk", "macosx", "--show-sdk-path").resolve(strict=True)
    harness_sources = (diff.FUNCTION_DIR / "reconstruction/harness.cpp",
                       diff.FUNCTION_DIR / "reconstruction/arith_literals.cpp")
    harness_inputs = directory / "harness"
    harness_inputs.mkdir()
    for source in harness_sources:
        body = source.read_bytes()
        (harness_inputs / source.name).write_bytes(body)
        closure[str(source.relative_to(ROOT))] = sha256_bytes(body)
    build = directory / "build"
    build.mkdir()
    executable = build / "function-oracle"
    flags = ("-std=c++20", "-O0", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=undefined", "-fno-sanitize-recover=all")
    command = [compiler, *flags, "-isysroot", sdk, "-I", (candidate / FUNCTION_SOURCES[0]).parent,
               candidate / FUNCTION_SOURCES[0], *(harness_inputs / item.name for item in harness_sources),
               "-o", executable]
    linked = qualification_sandbox.run(
        command, writable=build, readable=(candidate, harness_inputs, sdk, compiler.parent.parent),
        tools=(compiler.parent / "ld",), timeout=300)
    if linked.returncode:
        return {"verdict": "execution_failure",
                "problems": [f"function compiler exit {linked.returncode}: "
                             + (linked.stderr or linked.stdout)[-2500:]]}

    execution = directory / "worker"
    execution.mkdir()
    resource_dir = Path(subprocess.check_output(
        [str(compiler), "-print-resource-dir"], text=True, timeout=30).strip())
    runtime = (resource_dir / "lib/darwin/libclang_rt.ubsan_osx_dynamic.dylib").resolve(strict=True)
    run = functools.partial(qualification_sandbox.run, writable=execution, readable=(runtime,))
    observations = directory / "observations.jsonl"
    actual = []

    def observe(args, *, input, timeout, **_kwargs):
        completed = run(args, input=input, timeout=timeout)
        if completed.returncode != 0:
            raise QualificationError(completed.stderr[-2500:] or "function worker exited nonzero")
        if any(execution.iterdir()):
            raise QualificationError("function worker produced unexpected files")
        actual.append(completed.stdout)
        return completed

    executable_digest = sha256_file(executable)
    binding = {
        "candidate": {"source_digests": closure, "executable_sha256": executable_digest,
                      "architecture": platform.machine()},
        "recipe": {"compiler_sha256": sha256_file(compiler), "sdk": str(sdk), "flags": list(flags),
                   "sanitizer_sha256": sha256_file(runtime)},
        "environment": {"macos": platform.mac_ver()[0], "os_build": platform.version()},
        "reference": references,
        "validation_contract": {name: sha256_file(ROOT / name) for name in (
            "automation/qualification_sandbox.py", "automation/gfx_revision.py",
            "reverse_engineering/reconstruction_diff.py",
            "reverse_engineering/reconstruction_pilot.py",
            "reverse_engineering/macho_rva.py", "reverse_engineering/paths.py",
            "reverse_engineering/target.json")},
    }
    if previous is not None and (reused := _applicable_execution(previous, binding)) is not None:
        return {
            **{key: reused[key] for key in ("verdict", "evidence_tier", "scope",
                                           "coverage", "outputs", "execution",
                                           "execution_revision", "problems") if key in reused},
            **binding, "reused_execution_receipt": str(previous),
        }
    failures = diff.compare_corpus(executable, corpus, reference, runner=observe)
    observations.write_text("".join(json.dumps(row) + "\n" for row in actual))
    if sha256_file(executable) != executable_digest:
        raise QualificationError("function executable changed during execution")
    return {
        "verdict": "different" if failures else "exact",
        "evidence_tier": "emulated-original",
        "execution": "completed",
        "coverage": [row["id"] for row in corpus["scenarios"]],
        "outputs": [{"file": observations.name, "bytes": observations.stat().st_size,
                     "sha256": sha256_file(observations)}],
        "scope": {"function": "_GfxSetConstantBuffer", "cases": len(corpus["scenarios"]),
                  "memory_window": diff.common_window(corpus), "return_register": "RAX"},
        **binding,
        "problems": failures,
    }


def _reference(policy: dict, reference: dict, workspace: Path):
    from automation import case_locator, gfx_prefix_check, m6_frame

    matches = []
    roots = policy.get("evidence_roots")
    if not isinstance(roots, list) or not roots:
        return None, {"verdict": "missing_original_observation",
                      "problems": ["operator evidence roots are unconfigured"]}
    for root in roots:
        root_path = _outside(Path(root), workspace)
        located = case_locator.locate(root_path, key=reference["key"])
        if located["state"] in ("root_unavailable", "unknown"):
            continue
        if located["state"] == "missing_payload":
            return None, {"verdict": "missing_original_payload",
                          "problems": [f"{root_path}: locator payload is missing"]}
        if not located["safe_to_reuse"]:
            return None, {"verdict": "invalid_provenance",
                          "problems": [f"{root_path}: {located['state']}"]}
        matches.extend(located["matches"])
    if len(matches) != 1:
        return None, {"verdict": "invalid_provenance" if matches else "missing_original_observation",
                      "problems": ["trusted reference has no unique verified locator"]}
    row = matches[0]
    if any(row["identity"].get(key) != reference.get(key)
           for key in ("manifest_sha256", "seal_sha256")):
        return None, {"verdict": "invalid_provenance", "problems": ["locator identity differs"]}
    root = _outside(Path(row["resolved_path"]), workspace)
    frame, _ = gfx_prefix_check.trusted_frame_path(root, portable=True)
    trusted = gfx_prefix_check.verify_trusted_evidence(root, frame)
    trace = m6_frame.load_trace(frame)
    seal = m6_frame.load_seal(frame, trace=trace, trusted_evidence=trusted)
    return (frame, trace, seal, trusted), None


def _gl_preflight(policy: dict, case: dict, workspace: Path) -> dict:
    from automation.gfx_gl_control_qualification import preflight_reference

    source, refusal = _reference(policy, case["reference"], workspace)
    if refusal is not None:
        return refusal
    facts = preflight_reference(*source, case["selection"])
    _, trace, _, _ = source
    original_gl = trace.get("gl") or {}
    environment = {
        "architecture": trace.get("process_architecture"),
        **{key: original_gl.get(key) for key in ("vendor", "renderer", "version", "cgl")},
    }
    if facts["verdict"] == "eligible":
        if environment["architecture"] not in ("arm64", "x86_64"):
            facts["verdict"] = "missing_original_observation"
            facts["problems"].append("original process architecture is unobserved")
        if (not all(environment[key] for key in ("vendor", "renderer", "version"))
                or not (environment["cgl"] or {}).get("profile")):
            facts["verdict"] = "missing_original_observation"
            facts["problems"].append("original GL driver or context is unobserved")
    return {**facts, "reference": case["reference"], "evidence_tier": "original-eu4",
            "original_environment": environment,
            "original_evidence_verified": facts["verdict"] != "invalid_provenance",
            "execution": "not_started"}


def _selected_gl(commit: str, directory: Path, package: Path, expected: Path,
                 expectation: dict) -> dict:
    """Execute the compiled candidate with trusted GL draw/readback hooks."""
    from automation import gfx_gl_control_qualification as gl
    from automation import gfx_gl_control_renderer as driver
    from automation import gfx_gl_replay_input as inputs, launch_lease

    package_record = inputs.verify_package(package)
    reference_sha = sha256_file(expected)
    if (package_record["package_sha256"] != expectation["package_sha256"]
            or reference_sha != expectation["expected_sha256"]):
        return {"verdict": "invalid_provenance", "problems": ["GL fixture digest differs"]}
    manifest = package_record["manifest_document"]
    if any(item["sha256"] == reference_sha for item in manifest["files"]):
        return {"verdict": "invalid_provenance", "problems": ["GL reference bytes overlap input"]}
    width, height = (manifest["identity"][name] for name in ("width", "height"))
    pre = package / manifest["prestate"]["color"]["path"]
    if sha256_file(pre) == reference_sha:
        return {"verdict": "inapplicable_reference", "problems": ["draw has no attachment effect"]}
    reference = expected.read_bytes()
    control = directory / "control.ctl"
    control.write_text("\n".join(driver._ctl_lines(manifest, package)) + "\n")

    input_source = "renderer/gl/eu4_gfx_draw_control.c"
    staged = directory / "candidate"
    digests = candidate_snapshot.materialize_commit(ROOT, commit, staged, (input_source,))
    observer = ROOT / "renderer/gl/eu4_gfx_draw_observer.c"
    digests[str(observer.relative_to(ROOT))] = sha256_file(observer)
    clang = _xcrun("--find", "clang")
    sdk = _xcrun("--sdk", "macosx", "--show-sdk-path").resolve(strict=True)
    build = directory / "build"
    build.mkdir()
    executable = build / "gl-candidate"
    target = expectation["architecture"]
    if target not in ("arm64", "x86_64"):
        raise QualificationError("unsupported OpenGL build architecture")
    flags = ("-arch", target, "-isysroot", sdk, "-O2", "-Wno-deprecated-declarations")
    redirects = ("-Dmain=eu4_candidate_draw_main",
                 "-DglDrawArrays=eu4_observed_draw_arrays",
                 "-DglDrawElements=eu4_observed_draw_elements")
    candidate_object = build / "renderer.o"
    for arguments in (
            [*redirects, "-c", staged / input_source, "-o", candidate_object],
            [observer, candidate_object, "-framework", "OpenGL", "-o", executable]):
        built = qualification_sandbox.run(
            [clang, *flags, *arguments], writable=build,
            readable=(staged, observer, sdk, clang.parent.parent),
            tools=(clang.parent / "ld",), timeout=300)
        if built.returncode:
            return {"verdict": "execution_failure", "problems": [built.stderr[-2500:]]}

    results = {}
    for mode in ("draw", "omit"):
        work = directory / mode
        work.mkdir()
        with launch_lease.gpu_execution():
            observed = qualification_sandbox.run(
                [executable, mode, width, height, "--control", control, "--out", work],
                writable=work, readable=(package, control), graphics="gl")
        allowed = {"execution.json", "result.bgra", "observed-restore.bgra",
                   "observed-post.bgra", "observed-context.txt", "depth-after.f32", "stencil-after.u8"}
        if observed.returncode or any(
                path.name not in allowed or path.is_symlink() or not path.is_file()
                or path.stat().st_nlink != 1 for path in work.iterdir()):
            return {"verdict": "execution_failure", "problems": [
                f"GL worker exit {observed.returncode}: {observed.stderr[-2500:]}"]}
        execution = json.loads((work / "execution.json").read_text())
        if execution.get("execution") != "completed":
            return {"verdict": "unsupported_candidate",
                    "problems": execution.get("missing") or [str(execution)]}
        rows = [json.loads(line) for line in observed.stdout.splitlines()]
        if [row.get("phase") for row in rows] != ["restore", "post"]:
            return {"verdict": "execution_failure", "problems": [
                "trusted observer did not report exactly one draw boundary"]}
        actual = {key: row for key, row in zip(("restore", "post"), rows, strict=True)}
        restore = gl.compare(pre, work / "observed-restore.bgra", width, height)
        compared = gl.compare_payloads(
            reference, (work / "observed-post.bgra").read_bytes(), width, height)
        observations = work / "trusted-observations.json"
        observations.write_text(json.dumps(actual) + "\n")
        receipt, _ = dsh_receipts.write_receipt(work, {
            "schema": SCHEMA, "execution": "completed", "execution_revision": commit,
            "scope": "synthetic-selected-draw", "control": mode, "architecture": target,
            "source_digests": digests, "executable_sha256": sha256_file(executable),
            "recipe": {"compiler_sha256": sha256_file(clang), "sdk": str(sdk),
                       "flags": [*map(str, flags), *redirects]},
            "package_sha256": package_record["package_sha256"],
            "outputs": [{"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                        for path in sorted(work.iterdir())],
        })
        environment = dict(zip(("vendor", "renderer", "version", "context_profile"),
                               (work / "observed-context.txt").read_text().splitlines(), strict=True))
        results[mode] = {"observation": actual, "environment": environment,
                         "restore": restore, "post": compared, "receipt": str(receipt),
                         "post_equals_seed": sha256_file(work / "observed-post.bgra") == sha256_file(pre),
                         "renderer_artifact_sha256": sha256_file(work / "result.bgra")}


    draw, omission = results["draw"], results["omit"]
    problems = []
    earliest = None
    for phase in ("restore", "post"):
        for field, expected_value in expectation["observed_state"].get(phase, {}).items():
            actual_value = draw["observation"][phase].get(field)
            if expected_value != actual_value:
                earliest = earliest or {"phase": phase, "field": field,
                                        "expected": expected_value, "actual": actual_value}
                problems.append(f"{phase}.{field} differs")
    for result in results.values():
        if any(result["environment"].get(key) != value
               for key, value in expectation["gl"].items()):
            return {"verdict": "candidate_environment_mismatch", "problems": ["observed GL environment differs"]}
    if (not draw["restore"]["exact"] or not omission["restore"]["exact"]
            or draw["observation"]["restore"] != omission["observation"]["restore"]
            or omission["observation"]["post"]["submitted"] != 0
            or not omission["post_equals_seed"] or omission["post"]["exact"]):
        problems.append("draw omission failed to preserve restoration and demonstrate inequality")
    if draw["observation"]["post"]["submitted"] != 1 or not draw["post"]["exact"]:
        problems.append("draw output or command submission differs")
    return {
        "verdict": "different" if problems else "exact", "evidence_tier": "synthetic",
        "execution": "completed", "candidate": {
            "executable_sha256": sha256_file(executable), "source_digests": digests,
            "architecture": target,
        },
        "scope": {"observed_state": expectation["observed_state"], "attachment": "BGRA8"},
        "coverage": ["restoration", "submission", "post-attachment", "draw-omission"],
        "earliest_observed_divergence": earliest, "controls": results, "problems": problems,
        "reference": {"package_sha256": expectation["package_sha256"],
                      "expected_sha256": reference_sha},
    }


def _metal_prefix(policy: dict, case: dict, workspace: Path, commit: str,
                  directory: Path) -> dict:
    from automation import gfx_prefix_check, replay_host_dependencies as host

    source, refusal = _reference(policy, case["reference"], workspace)
    if refusal is not None:
        return refusal
    frame, _, _, trusted = source
    sources = host.candidate_sources(ROOT, policy["trusted_revision"])
    closure = host.dependency_paths(ROOT, sources, strict=True)
    entry_point = sources[1]
    snapshot = directory / "sources"
    digests = candidate_snapshot.materialize_commit(
        ROOT, commit, snapshot, tuple(path for path in closure if path != entry_point))
    entry = snapshot / entry_point
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes((ROOT / entry_point).read_bytes())
    digests[entry_point] = sha256_file(entry)

    clang = _xcrun("--find", "clang")
    sdk = _xcrun("--sdk", "macosx", "--show-sdk-path").resolve(strict=True)
    build = directory / "build"
    build.mkdir()
    executable = build / "metal-replay"
    flags = host.BUILD_FLAGS
    linked = qualification_sandbox.run(
        [clang, *flags, "-isysroot", sdk, "-o", executable,
         *(snapshot / path for path in sources)],
        writable=build, readable=(snapshot, sdk, clang.parent.parent),
        tools=(clang.parent / "ld",), timeout=300)
    if linked.returncode:
        return {"verdict": "execution_failure", "problems": [
            f"Metal compiler exit {linked.returncode}: " + (linked.stderr or linked.stdout)[-2500:]]}
    producer = {"revision": commit, "source_digests": digests,
                "build": {"host_sha256": sha256_file(executable)},
                "architecture": platform.machine()}
    result = gfx_prefix_check.evaluate(
        frame, trusted_root=trusted["_root"], trusted_execution=True,
        require_end=case["require_end"], trusted_run_dir=directory,
        fenced_host=(executable, producer))
    observed = {row["event"]: row for row in result.get("events", [])
                if row.get("status") in ("match", "divergent")
                and row.get("independently_verified")}
    exact_regressions = [event for event in case.get("preserve_exact", [])
                         if observed.get(event, {}).get("status") != "match"]
    if result.get("problems") or result.get("failed_execution"):
        verdict = "execution_failure"
    elif not result.get("independent_equality") or exact_regressions:
        verdict = "regression"
    elif result.get("qualified"):
        verdict = "exact"
    elif result.get("verdict") == "FIRST_DIVERGENCE":
        verdict = "different"
    else:
        verdict = "unsupported"
    event = result.get("first_divergence")
    return {
        "verdict": verdict, "evidence_tier": "original-eu4",
        "execution": "completed" if result.get("independent_equality") else "failed",
        "reference": case["reference"], "coverage": sorted(observed),
        "candidate": producer, "scope": {"through": case["require_end"]},
        "earliest_observed_divergence": observed.get(event),
        "preserved_exact": [n for n in observed if observed[n]["status"] == "match"],
        "execution_dir": (result.get("execution_dir")
                          or (result.get("failed_execution") or {}).get("execution_dir")),
        "problems": result.get("problems", []) + [
            f"previously exact event {required} is {observed.get(required, {}).get('status', 'unverified')}"
            for required in exact_regressions],
    }


def _passes(case: dict, row: dict) -> bool:
    required_coverage = set(case.get("minimum_coverage", []))
    if not required_coverage.issubset(row.get("coverage", [])):
        return False
    if row["verdict"] == "exact":
        return True
    if case["category"] != "diagnostic":
        return False
    if row["verdict"] == "different":
        return bool(required_coverage)
    return (row["verdict"] in ("missing_original_observation", "inapplicable_reference")
            and row.get("original_evidence_verified") is True
            and bool(row.get("problems"))
            and set(row["problems"]).issubset(case.get("original_limitations", [])))


def qualify(workspace: Path | str, commit: str, case_id: str | None = None) -> dict:
    workspace = Path(workspace).expanduser().resolve(strict=True)
    policy, policy_digest = _policy(workspace, commit)
    cases = policy.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise QualificationError("protected case inventory is missing")
    if any(not isinstance(row, dict) or row.get("category") not in
           ("exact", "diagnostic", "optional") for row in cases.values()):
        raise QualificationError("protected case category is invalid")
    required = [key for key, row in cases.items() if row["category"] != "optional"]
    if case_id is not None and case_id not in cases:
        raise QualificationError("case id is absent from the operator policy")
    selected = [case_id] if case_id is not None else required
    run_root = _outside(Path(policy["run_root"]), workspace)
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="qualification-", dir=run_root))
    tree = _git("rev-parse", f"{commit}^{{tree}}")
    results = []
    for index, key in enumerate(selected):
        case = cases[key]
        job = directory / f"case-{index}"
        job.mkdir()
        try:
            if case["kind"] == "constant-buffer":
                previous = None
                if case.get("reuse_receipt"):
                    previous = run_root / case["reuse_receipt"]
                    if not previous.resolve().is_relative_to(run_root):
                        raise QualificationError("execution receipt escapes the protected run root")
                row = qualify_function(commit, job, case["reference"], previous=previous)
            elif case["kind"] == "synthetic-gl":
                reference = case["reference"]
                row = _selected_gl(commit, job, _outside(Path(reference["package"]), workspace),
                                   _outside(Path(reference["expected"]), workspace), reference)
            elif case["kind"] == "gl":
                row = _gl_preflight(policy, case, workspace)
            elif case["kind"] == "metal-prefix":
                row = _metal_prefix(policy, case, workspace, commit, job)
            else:
                row = {"verdict": "unsupported", "problems": ["case executor is unavailable"]}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            row = {"verdict": "execution_failure", "problems": [str(exc)]}
        receipt = row.pop("reused_execution_receipt", None)
        if receipt is None:
            receipt, _ = dsh_receipts.write_receipt(job, {
                "schema": SCHEMA, "nonce": directory.name, "case_id": key,
                "execution_revision": commit, "tree": tree, "policy_sha256": policy_digest,
                "validator_revision": policy["trusted_revision"], **row,
            })
        else:
            row["execution_reused"] = True
        results.append({"case_id": key, "category": case["category"],
                        "receipt": str(receipt),
                        "requirement_satisfied": _passes(case, row), **row})
    authentic = policy.get("authentic_case")
    authentic_pass = any(
        row["case_id"] == authentic and row["category"] == "exact"
        and cases[authentic]["kind"] == "gl"
        and row.get("evidence_tier") == "original-eu4" and row["verdict"] == "exact"
        and row.get("execution") == "completed"
        for row in results)
    qualified = case_id is None and authentic_pass and all(
        row["requirement_satisfied"] for row in results)
    result = {
        "schema": SCHEMA, "revision": commit, "tree": tree, "policy_sha256": policy_digest,
        "mode": "focused" if case_id is not None else "mandatory",
        "required_cases": required, "executed_cases": [row["case_id"] for row in results
                           if row.get("execution") == "completed" and not row.get("execution_reused")],
        "cases": results, "qualified": qualified,
    }
    path, _ = dsh_receipts.write_receipt(directory, result)
    return {**result, "receipt": str(path)}
