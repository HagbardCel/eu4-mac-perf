"""Driver for the P3-B OpenCode pilot, pre-registration and acceptance run.

This is the *only* entry point that launches the pinned binary. It is deliberately
boring: it resolves the declared layout, constructs the adapter, hands control to
the controller, and reports what happened. It never touches EU IV, never certifies
by itself, and never edits a sealed receipt.

Three modes:

``preregister``
    Emit the pre-registration - the identities and parameters that must be
    committed **before** the run. It is computed on the same code path the run
    uses, so the pinned digests cannot drift from what the run actually binds. No
    agent, no model, no network.

``pilot``
    Exercise the binding end to end against a **visible-only** fixture root,
    ending in a code-level ``gave_up`` so the controller can never spend the
    held-out corpus. It establishes connectivity, the tool catalog, a real
    permission denial and the observed identities. **Non-accepting by
    construction.**

``acceptance``
    The one declared evaluation: the repair prompt, a bounded wall clock, a single
    attempt. It spends the holdout exactly once (only after the visible suite
    passes) and seals the terminal receipt.

Layout: ``--staging-root`` is absolute and outside the repository;
``run_dir = <staging-root>/<run-id>`` and the agent's workspace is its
``workspace/`` child.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from oracle.slice import budget, compare, controller, corpus, harness, opencode, receipts
from oracle.slice.controller import RunConfig
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(__file__).resolve().parent / "opencode_config"
DEFAULT_STAGING = Path.home() / ".local" / "share" / "eu4-oracle-p3"
DEFAULT_MODEL = {"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"}
PREREGISTRATION_SCHEMA = "eu4-oracle-preregistration/v1"

SOURCE_PATHS = {
    "controller_sha256": "oracle/slice/controller.py",
    "adapters_sha256": "oracle/slice/adapters.py",
    "harness_sha256": "oracle/slice/harness.py",
    "comparator_sha256": "oracle/slice/compare.py",
    "driver_sha256": "oracle/slice/driver.c",
    "interface_sha256": "oracle/slice/synth.h",
    "bridge_sha256": "oracle/slice/bridge.py",
    "opencode_sha256": "oracle/slice/opencode.py",
    # The semantic closure the guard replays with, plus the launch driver: if any
    # of these is not pinned, a replay could be computed by code the run was never
    # evaluated with.
    "core_sha256": "oracle/core.py",
    "corpus_sha256": "oracle/slice/corpus.py",
    "receipts_sha256": "oracle/slice/receipts.py",
    "launch_driver_sha256": "oracle/slice/opencode_run.py",
}


def _default_run_id(mode: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"p3b-{mode}-{stamp}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oracle.slice.opencode_run")
    parser.add_argument("--mode", choices=("preregister", "pilot", "acceptance"),
                        required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING))
    parser.add_argument("--model", default=f"{DEFAULT_MODEL['provider']}/{DEFAULT_MODEL['id']}"
                                           f"#{DEFAULT_MODEL['variant']}")
    parser.add_argument("--binary", default="/opt/homebrew/bin/opencode")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--budget-calls", type=int, default=None)
    parser.add_argument("--wall-seconds", type=float, default=None)
    parser.add_argument("--finalization-seconds", type=float, default=300.0)
    parser.add_argument("--expected-binary-sha256", default=None)
    parser.add_argument("--expected-version", default=None)
    parser.add_argument("--preregistration", default=None,
                        help="a committed pre-registration the run must conform to")
    parser.add_argument("--out", default=None, help="where preregister writes its JSON")
    parser.add_argument("--prompt", choices=("auto", "repair", "pilot"), default="auto",
                        help="which task prompt the agent runs; 'auto' follows the mode")
    parser.add_argument("--candidate", default=str(REPO_ROOT / "oracle" / "slice" / "target.c"))
    return parser


def _parse_model(text: str) -> tuple[str, str, str | None]:
    base, _, variant = text.partition("#")
    provider, sep, model_id = base.partition("/")
    if not sep or not provider or not model_id:
        raise SystemExit(f"--model must be provider/model[#variant], got {text!r}")
    return provider, model_id, (variant or None)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _repo_relative(path: Path) -> str:
    """A repository-relative path when possible, so pins avoid machine paths."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _repo_path(path: Path) -> str | None:
    """The repository-relative posix path, or None if it escapes the repository."""
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None


def _effective_limits(args, mode: str) -> dict:
    """The run bounds as the driver will actually resolve them.

    A declaration records *resolved* numbers, so the same resolution must check
    it: an omitted optional flag means the mode default, not ``None``. Generation
    and enforcement must agree exactly, or a normal invocation is refused.
    """
    return {
        "budget_calls": args.budget_calls if args.budget_calls is not None
        else (24 if mode == "pilot" else 40),
        "wall_seconds": args.wall_seconds if args.wall_seconds is not None
        else (900.0 if mode == "pilot" else 3600.0),
        "finalization_seconds": args.finalization_seconds,
    }


def _visible_verdict(candidate: Path, run_id: str) -> dict:
    """Compile and evaluate the agent's candidate against the visible corpus.

    Used only by the non-accepting modes, to record whether the agent really
    changed the candidate into something that matches the visible behaviour -
    without touching the held-out corpus.
    """
    if not Path(candidate).is_file():
        return {"status": "no_candidate"}
    fixtures = corpus.load_corpus(corpus.VISIBLE)
    with tempfile.TemporaryDirectory() as tmp:
        build = harness.build(Path(candidate), Path(tmp) / "build", cc="cc")
        diagnostics = [harness.evaluate(build, fixture, run_id=run_id,
                                       timeout_s=harness.DEFAULT_TIMEOUT_S)
                       for fixture in fixtures]
    return compare.verdict(diagnostics)


def _preregistration(*, run_id: str, staging_root: Path, args, provider: str, model_id: str,
                     variant: str) -> dict:
    """Compute the pin on the same code path the run binds.

    The materialized configuration digest depends on the absolute run directory,
    so the run id and staging root are part of the declaration - which is why the
    staging base is preallocated at a stable path rather than chosen at run time.
    """
    run_dir = staging_root / run_id
    model = f"{provider}/{model_id}" + (f"#{variant}" if variant else "")
    template_path = CONFIG_DIR / opencode.CONFIG_TEMPLATE
    template = template_path.read_text(encoding="utf-8")
    substitutions = {
        "@MODEL@": model,
        "@PYTHON@": str(Path(args.python)),
        "@REPO_ROOT@": str(REPO_ROOT),
        "@RUN_DIR@": str(run_dir),
        "@WORKSPACE@": str(run_dir / "workspace"),
    }
    materialized = opencode.materialize(template, substitutions)

    binary = Path(args.binary)
    probe = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                           timeout=120, check=False)
    version = next((line.strip() for line in f"{probe.stdout}\n{probe.stderr}".splitlines()
                    if line.strip()), "")
    if probe.returncode != 0 or not version:
        raise SystemExit(f"cannot establish the OpenCode version from {binary}")

    corpus_manifests = {}
    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        corpus_manifests[set_name] = corpus.manifest(corpus.load_corpus(set_name),
                                                     set_name=set_name)

    return {
        "schema": PREREGISTRATION_SCHEMA,
        "declared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "acceptance",
        "run_id": run_id,
        "staging_root": str(staging_root),
        "repo_root": str(REPO_ROOT),
        "python": str(Path(args.python)),
        "binary": {"path": str(binary), "sha256": _sha256_file(binary), "version": version},
        "model": {"provider": provider, "id": model_id, "variant": variant},
        "prompt_path": "oracle/slice/opencode_config/prompt.txt",
        "prompt_sha256": _sha256_file(CONFIG_DIR / opencode.REPAIR_PROMPT),
        "config": {
            "template_path": "oracle/slice/opencode_config/opencode.json.template",
            "template_sha256": _sha256_file(template_path),
            "materialized_sha256": hashlib.sha256(materialized.encode("utf-8")).hexdigest(),
        },
        "substitutions": substitutions,
        "corpus": corpus_manifests,
        "source": {
            "candidate_path": _repo_relative(Path(args.candidate)),
            "defective_candidate_sha256": _sha256_file(Path(args.candidate)),
            "baseline_commit": _git("rev-parse", "HEAD"),
        },
        "source_hashes": {key: _sha256_file(REPO_ROOT / rel)
                          for key, rel in SOURCE_PATHS.items()},
        "run": {
            "mode": "acceptance",
            "attempts": 1,
            **_effective_limits(args, "acceptance"),
        },
        "conditions": [
            "C1 the pinned binary, model, variant and effective configuration are bound",
            "C2 the pinned process enforces its restrictions on its direct tools, with a "
            "structured denial distinguishable from absence, refusal and failure",
            "C3 held-out fixtures are absent from the workspace and never intentionally "
            "exposed through the agent-facing API",
            "C3b no held-out identifier or fingerprint appears in any agent-visible surface",
            "C4 ledger-authoritative accounting plus event-level proof of a successful "
            "oracle_inspect and oracle_test",
            "C5 the final candidate differs, passes visible and held-out, certification ran",
            "C6 the sealed receipt and committed artifact replay under the model-free guard",
            "C7 the run is bounded and not aborted",
            "C8 this pre-registration was committed before the run",
            "C9 scope is stated honestly",
        ],
        "acceptance_memo": "docs/oracle/P3B_ACCEPTANCE.md",
    }


def _visible_only_root(run_dir: Path) -> Path:
    """A fixture root that contains the visible set and nothing else.

    The pilot and rehearsal modes must not even be able to *name* the held-out
    corpus. The bridge and the workspace loader resolve fixtures under the root
    they are given, so a visible-only root removes the held-out directory from the
    run's reachable world instead of merely declining to read it.
    """
    root = Path(run_dir) / "visible-only"
    target = root / corpus.VISIBLE
    if not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(corpus.FIXTURES_DIR / corpus.VISIBLE, target)
    return root


def _git(*arguments: str) -> str:
    probe = subprocess.run(["git", "-C", str(REPO_ROOT), *arguments],
                           capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise SystemExit(f"git {' '.join(arguments)} failed: {probe.stderr.strip()}")
    return probe.stdout.strip()


def _git_bytes(*arguments: str) -> bytes | None:
    """Raw ``git`` stdout, or None when the command fails.

    The bytes are deliberately not stripped: they are compared against committed
    blobs, where a trailing newline matters.
    """
    probe = subprocess.run(["git", "-C", str(REPO_ROOT), *arguments],
                           capture_output=True, check=False)
    return probe.stdout if probe.returncode == 0 else None


def _git_ok(*arguments: str) -> bool:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *arguments],
                          capture_output=True, check=False).returncode == 0


def _contract_problems(declared: dict, args, *, prereg_path: Path) -> list[str]:
    """Why the working tree does not match the declaration, if it does not.

    ``baseline_commit`` names the *source revision* the declaration was generated
    on. Committing the declaration afterwards necessarily advances ``HEAD``, so the
    contract is not "HEAD equals A" (which would force the declaration to be left
    uncommitted) but:

    * A is an ancestor of the checkout, and every pinned source hashes to the
      declared value both at A and in the working tree;
    * the working tree is clean;
    * the declaration is a *committed* object at HEAD - byte-identical to
      ``git cat-file blob HEAD:<path>`` - rather than an arbitrary readable file;
    * the candidate is the declared path (inside the repository, not a symlink)
      with the declared content, and the interpreter and bounds are the declared
      ones.
    """
    source = declared.get("source")
    if not isinstance(source, dict):
        return ["the declaration predates the source-revision pin (no 'source' block)"]
    problems: list[str] = []
    head = _git("rev-parse", "HEAD")
    baseline = source.get("baseline_commit")
    if not isinstance(baseline, str) or not baseline:
        problems.append("the declaration records no baseline_commit")
    elif not _git_ok("merge-base", "--is-ancestor", baseline, "HEAD"):
        problems.append(
            f"the declared baseline_commit {baseline!r} is not an ancestor of HEAD {head!r}")

    dirty = _git("status", "--porcelain")
    if dirty:
        problems.append("the working tree is not clean: " + dirty.replace("\n", "; "))

    # The declaration itself must be committed. This is the strongest statement an
    # offline check can make: that the file is a Git object in this checkout. It
    # does *not* prove the commit was pushed before the run; that remains a
    # documented process fact.
    relative = _repo_path(prereg_path)
    if relative is None:
        problems.append("the pre-registration is not inside the repository")
    else:
        committed = _git_bytes("cat-file", "blob", f"HEAD:{relative}")
        if committed is None:
            problems.append(f"the pre-registration {relative} is not committed at HEAD")
        elif committed != Path(prereg_path).read_bytes():
            problems.append("the pre-registration differs from its committed blob")

    candidate = Path(args.candidate)
    actual_path = _repo_path(candidate)
    if actual_path is None:
        problems.append("the candidate source is not inside the repository")
    elif actual_path != source.get("candidate_path"):
        problems.append(
            f"the candidate source path {actual_path!r} is not the declared "
            f"{source.get('candidate_path')!r}")
    if candidate.is_symlink():
        problems.append("the candidate source is a symlink")
    if source.get("defective_candidate_sha256") != _sha256_file(candidate):
        problems.append("the candidate source content differs from the declaration")

    if declared.get("python") != str(Path(args.python)):
        problems.append("the interpreter differs from the declaration")

    source_hashes = declared.get("source_hashes") or {}
    for key, rel in SOURCE_PATHS.items():
        wanted = source_hashes.get(key)
        if wanted is None:
            problems.append(f"the declaration does not pin {rel}")
            continue
        if wanted != _sha256_file(REPO_ROOT / rel):
            problems.append(f"{rel} differs from the declaration in the working tree")
        if isinstance(baseline, str) and baseline:
            blob = _git_bytes("cat-file", "blob", f"{baseline}:{rel}")
            if blob is None:
                problems.append(f"{rel} is not present at the declared baseline_commit")
            elif hashlib.sha256(blob).hexdigest() != wanted:
                problems.append(f"{rel} differs from the declaration at the baseline_commit")

    limits = _effective_limits(args, args.mode)
    run = declared.get("run") or {}
    for key in ("budget_calls", "wall_seconds", "finalization_seconds"):
        if run.get(key) != limits[key]:
            problems.append(
                f"run.{key}={limits[key]!r} differs from the declared {run.get(key)!r}")
    return problems


def _enforce_preregistration(path: Path, *, run_id: str, staging_root: Path,
                             model: dict, args, strict: bool,
                             prompt_path: Path) -> dict:
    """Refuse to run under anything other than the declared pin."""
    declared = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    if declared.get("run_id") != run_id:
        problems.append(f"run_id {run_id!r} != declared {declared.get('run_id')!r}")
    if declared.get("staging_root") != str(staging_root):
        problems.append("staging root differs from the declaration")
    if declared.get("model") != model:
        problems.append("model differs from the declaration")
    if strict:
        # The prompt is part of the execution contract: the run must not launch
        # with a prompt the declaration did not name. This is checked before the
        # adapter is built, so a mismatched prompt never reaches the model.
        actual_prompt = _repo_relative(prompt_path)
        if actual_prompt != declared.get("prompt_path"):
            problems.append(
                f"the prompt {actual_prompt!r} is not the declared "
                f"{declared.get('prompt_path')!r}")
        elif declared.get("prompt_sha256") != _sha256_file(prompt_path):
            problems.append("the prompt content differs from the declaration")
        problems.extend(_contract_problems(declared, args, prereg_path=Path(path)))
    if problems:
        raise SystemExit("run does not conform to the pre-registration: " + "; ".join(problems))
    args.expected_binary_sha256 = declared["binary"]["sha256"]
    args.expected_version = declared["binary"]["version"]
    # The declaration is authoritative for the bounds the receipt will record.
    run = declared.get("run") or {}
    for key in ("budget_calls", "wall_seconds", "finalization_seconds"):
        if isinstance(run.get(key), (int, float)) and not isinstance(run.get(key), bool):
            setattr(args, key, run[key])
    print(f"conforms to pre-registration {path}")
    return declared


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider, model_id, variant = _parse_model(args.model)
    model = {"provider": provider, "id": model_id, "variant": variant}
    run_id = args.run_id or _default_run_id(args.mode)
    staging_root = Path(args.staging_root).expanduser().resolve()
    run_dir = staging_root / run_id

    if args.mode == "preregister":
        declaration = _preregistration(run_id=run_id, staging_root=staging_root, args=args,
                                       provider=provider, model_id=model_id, variant=variant)
        text = json.dumps(declaration, indent=2, sort_keys=True) + "\n"
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(f"wrote {out}")
        print(text)
        return 0

    # The prompt is resolved *before* enforcement so its identity can be checked
    # against the declaration. Acceptance has no prompt flexibility: it runs the
    # one declared repair prompt, so substituting the pilot prompt is refused here,
    # before the adapter (and therefore the model) is ever constructed.
    prompt_choice = args.prompt
    if prompt_choice == "auto":
        prompt_choice = "pilot" if args.mode == "pilot" else "repair"
    if args.mode == "acceptance" and prompt_choice != "repair":
        raise SystemExit(
            "acceptance runs use the declared repair prompt; --prompt pilot is refused")
    prompt_path = CONFIG_DIR / (opencode.PILOT_PROMPT if prompt_choice == "pilot"
                                else opencode.REPAIR_PROMPT)

    declared: dict | None = None
    if args.preregistration:
        declared = _enforce_preregistration(Path(args.preregistration), run_id=run_id,
                                            staging_root=staging_root, model=model, args=args,
                                            strict=(args.mode == "acceptance"),
                                            prompt_path=prompt_path)
    if args.mode == "acceptance" and declared is None:
        # The declaration comes first, by construction: an acceptance run without
        # one would be evaluated against conditions nobody committed to.
        raise SystemExit("acceptance runs require --preregistration")

    # Record the commit that published the declaration (B) and its path, so a later
    # offline check can re-verify the committed pre-registration. A is the source
    # revision, recorded separately as ``baseline_commit``.
    declaration_commit: str | None = None
    preregistration_path: str | None = None
    if declared is not None and args.mode == "acceptance":
        declaration_commit = _git("rev-parse", "HEAD")
        preregistration_path = _repo_relative(Path(args.preregistration))

    adapter = OpenCodeAdapter(OpenCodeRun(
        run_id=run_id,
        run_dir=run_dir,
        workspace=run_dir / "workspace",
        repo_root=REPO_ROOT,
        python=Path(args.python),
        binary=Path(args.binary),
        provider=provider,
        model_id=model_id,
        variant=variant,
        prompt_path=prompt_path,
        # A non-accepting mode ends with a code-level ``gave_up`` so the controller
        # can never certify and the held-out corpus is never spent. Pairing it with
        # the repair prompt is a visible-only rehearsal of the real task.
        mode="probe" if args.mode == "pilot" else "repair",
        expected_binary_sha256=args.expected_binary_sha256,
        expected_version=args.expected_version,
    ))
    # Only the acceptance mode may resolve the held-out corpus at all; the pilot
    # and rehearsal modes get a visible-only fixture root.
    fixtures_root = None if args.mode == "acceptance" else _visible_only_root(run_dir)
    config = RunConfig(
        run_id=run_id,
        candidate_src=Path(args.candidate),
        base_dir=staging_root,
        budget_calls=args.budget_calls or (24 if args.mode == "pilot" else 40),
        max_attempts=1,
        wall_seconds=args.wall_seconds or (900.0 if args.mode == "pilot" else 3600.0),
        finalization_seconds=args.finalization_seconds,
        model=model,
        fixtures_root=fixtures_root,
        baseline_commit=(declared or {}).get("source", {}).get("baseline_commit")
        if declared else None,
        declaration_commit=declaration_commit,
        preregistration_path=preregistration_path,
        require_repair_acceptance=(args.mode == "acceptance"),
    )

    print(f"mode={args.mode} run_id={run_id}")
    print(f"staging_root={staging_root}")
    print(f"model={args.model}")
    print(f"binary={args.binary}")
    print(f"prompt={prompt_path.name}")
    result = controller.run(config, adapter)

    payload = result.receipt["payload"]
    binding = payload["toolchain"].get("opencode")
    summary = None
    try:
        summary = adapter.sanitized_summary()
    except Exception:
        pass
    if summary is not None:
        (run_dir / "agent.events.json").write_bytes(
            json.dumps(summary, indent=2, sort_keys=True).encode() + b"\n")

    report = {
        "mode": args.mode,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": result.status,
        "certification": result.certification,
        "candidate_verified": result.candidate_verified,
        "abort_reason": payload["execution"]["abort_reason"],
        "finalization_error": payload["execution"]["finalization"]["error"],
        "receipt_sha256": result.receipt["sha256"],
        "binding": binding,
        "journal": payload["agent"].get("journal"),
        "acceptance": payload.get("acceptance"),
        "oracle_tool_calls": (summary or {}).get("oracle_tool_calls"),
        "denials": (summary or {}).get("denials"),
        "event_types": (summary or {}).get("event_types"),
        "visible": (payload["verdict"].get("visible") or {}).get("status"),
        "held_out": (payload["verdict"].get("held_out") or {}).get("status"),
    }
    if args.mode != "acceptance":
        # Non-accepting modes never certify, so record the visible verdict of the
        # candidate the agent actually left behind.
        report["visible_after_attempt"] = _visible_verdict(
            run_dir / "workspace" / "candidate.c", run_id)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"receipt={run_dir / 'receipt.json'}")
    receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
    if args.mode == "acceptance" and result.status != controller.STATUS_CERTIFIED:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
