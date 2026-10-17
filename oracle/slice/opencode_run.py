"""Driver for the P3-B OpenCode pilot and the single declared acceptance run.

This is the *only* entry point that launches the pinned binary. It is deliberately
boring: it resolves the declared layout, constructs the adapter, hands control to
the controller, and reports what happened. It never touches EU IV, never
certifies by itself, and never edits a receipt.

Two modes:

``pilot``
    Exercises the binding end to end against a **visible-only** fixture root and
    ends with a code-level ``gave_up``, so the controller can never spend the
    held-out corpus. It establishes connectivity, the tool catalog, a real
    permission denial and the observed identities that the pre-registration must
    pin. **Non-accepting by construction.**

``acceptance``
    The one declared evaluation: the repair prompt, a bounded wall clock, and a
    single attempt. It spends the holdout exactly once (only after the visible
    suite passes) and seals the terminal receipt.

Layout: ``--staging-root`` is absolute and outside the repository;
``run_dir = <staging-root>/<run-id>`` and the agent's workspace is its
``workspace/`` child, so OpenCode cannot walk up into the repository's own
configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from oracle.slice import controller, receipts
from oracle.slice.controller import RunConfig
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGING = Path.home() / ".local" / "share" / "eu4-oracle-p3"
DEFAULT_MODEL = {"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"}


def _default_run_id(mode: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"p3b-{mode}-{stamp}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oracle.slice.opencode_run")
    parser.add_argument("--mode", choices=("pilot", "acceptance"), required=True)
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
    parser.add_argument("--candidate", default=str(REPO_ROOT / "oracle" / "slice" / "target.c"))
    return parser


def _parse_model(text: str) -> tuple[str, str, str | None]:
    base, _, variant = text.partition("#")
    provider, sep, model_id = base.partition("/")
    if not sep or not provider or not model_id:
        raise SystemExit(f"--model must be provider/model[#variant], got {text!r}")
    return provider, model_id, (variant or None)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider, model_id, variant = _parse_model(args.model)
    run_id = args.run_id or _default_run_id(args.mode)
    staging_root = Path(args.staging_root).expanduser().resolve()
    run_dir = staging_root / run_id

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
        model={"provider": provider, "id": model_id, "variant": variant},
    )

    print(f"mode={args.mode} run_id={run_id}")
    print(f"staging_root={staging_root}")
    print(f"model={args.model}")
    print(f"binary={args.binary}")
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
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"receipt={run_dir / 'receipt.json'}")
    receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
    if args.mode == "acceptance" and result.status != controller.STATUS_CERTIFIED:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
