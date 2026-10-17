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
import subprocess
import sys
import tempfile
from pathlib import Path

from oracle.slice import compare, controller, corpus, harness, opencode, receipts
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
        "source_hashes": {key: _sha256_file(REPO_ROOT / rel)
                          for key, rel in SOURCE_PATHS.items()},
        "run": {
            "mode": "acceptance",
            "attempts": 1,
            "budget_calls": args.budget_calls or 40,
            "wall_seconds": args.wall_seconds or 3600.0,
            "finalization_seconds": args.finalization_seconds,
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


def _enforce_preregistration(path: Path, *, run_id: str, staging_root: Path,
                             model: dict, args) -> None:
    """Refuse to run under anything other than the declared pin."""
    declared = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    if declared.get("run_id") != run_id:
        problems.append(f"run_id {run_id!r} != declared {declared.get('run_id')!r}")
    if declared.get("staging_root") != str(staging_root):
        problems.append("staging root differs from the declaration")
    if declared.get("model") != model:
        problems.append("model differs from the declaration")
    if problems:
        raise SystemExit("run does not conform to the pre-registration: " + "; ".join(problems))
    args.expected_binary_sha256 = declared["binary"]["sha256"]
    args.expected_version = declared["binary"]["version"]
    print(f"conforms to pre-registration {path}")


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

    if args.preregistration:
        _enforce_preregistration(Path(args.preregistration), run_id=run_id,
                                 staging_root=staging_root, model=model, args=args)

    prompt_choice = args.prompt
    if prompt_choice == "auto":
        prompt_choice = "pilot" if args.mode == "pilot" else "repair"
    prompt_path = CONFIG_DIR / (opencode.PILOT_PROMPT if prompt_choice == "pilot"
                                else opencode.REPAIR_PROMPT)

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
    config = RunConfig(
        run_id=run_id,
        candidate_src=Path(args.candidate),
        base_dir=staging_root,
        budget_calls=args.budget_calls or (24 if args.mode == "pilot" else 40),
        max_attempts=1,
        wall_seconds=args.wall_seconds or (900.0 if args.mode == "pilot" else 3600.0),
        finalization_seconds=args.finalization_seconds,
        model=model,
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
