#!/usr/bin/env python3
"""Guarded, model-free re-verification of published Oracle P3-B receipts.

CI must be able to re-check a published run without a model, without a network
and without the agent's cooperation. For every sealed receipt under
``docs/oracle/receipts/`` this guard:

1. **re-verifies the seal** - the digest is recomputed over the canonical
   payload, not read back;
2. **cross-checks the pre-registration** - the identities that were committed
   *before* the run, so a receipt cannot be reconciled to its declaration after
   the fact;
3. **re-materializes the agent configuration** from the tracked template plus the
   pre-registered substitution map, and requires the recorded digest to match;
4. **replays both corpora over the committed artifact** - *only after* an
   integrity gate refuses to replay when the current harness, comparator, driver,
   interface or corpus no longer match the pinned identities, because replaying
   against a changed oracle would prove nothing;
5. **scans the harness-authored identities for undeclared absolute paths** - the
   bound toolchain, agent, corpus, source and verdict objects must not name a
   machine path the declaration does not account for. Free-form strings the child
   produced (attempt notes, denial resources, error text) are necessarily
   arbitrary and are excluded, since flagging them would fail a run for an agent
   merely naming a path; the denials are separately required to be structured;
6. **scans the published, sanitized event summary** for held-out fingerprints.

With no published receipts it is a no-op, so it can land before the run it
guards. A pre-registration whose receipt is not published yet is reported as
**pending** rather than failed, because the declaration is deliberately committed
before the run; ``--require-receipts`` turns that into a failure. It exits
non-zero on the first violation.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from oracle.core import canonical_bytes  # noqa: E402
from oracle.slice import compare, corpus, harness, opencode, receipts  # noqa: E402

RECEIPTS_DIR = Path("docs/oracle/receipts")
ARTIFACTS_DIR = Path("docs/oracle/artifacts")
AGENTS_DIR = Path("docs/oracle/agents")
PREREGISTRATION_DIR = Path("docs/oracle/preregistration")

#: Absolute paths that may legitimately appear in a published receipt: system
#: locations named by the compiler probe, plus the run's declared staging root.
SYSTEM_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/etc/", "/opt/homebrew/", "/usr/local/",
    "/Applications/", "/System/", "/Library/", "/private/var/folders/",
    "/private/tmp/", "/tmp/", "/var/folders/",
)

_ABSOLUTE = None


def _absolute_regex():
    global _ABSOLUTE
    if _ABSOLUTE is None:
        import re
        _ABSOLUTE = re.compile(
            r'/(?:Users|home|Volumes|private|tmp|var|mnt|opt|usr|etc|Applications'
            r'|System|Library|bin|sbin)(?:/[^\s"\'\\,;:)\]}]*)')
    return _ABSOLUTE


class GuardError(RuntimeError):
    """A published receipt does not survive independent re-verification."""


class PendingReceipt(RuntimeError):
    """A pre-registration is committed but its receipt is not published yet.

    The declaration is deliberately committed *before* the run, so this is a
    normal intermediate state, not a violation - unless ``--require-receipts``
    says otherwise.
    """


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GuardError(f"{path} is not a JSON object")
    return data


def check_absolute_paths(serialized: str, allow_prefixes: tuple[str, ...]) -> list[str]:
    stray = []
    for path in sorted(set(_absolute_regex().findall(serialized))):
        if not any(path.startswith(prefix) for prefix in allow_prefixes):
            stray.append(path)
    return stray


def _cross_check(receipt: dict, prereg: dict) -> None:
    payload = receipt.get("payload") or {}
    run_id = prereg.get("run_id")
    if payload.get("run_id") != run_id:
        raise GuardError(f"receipt run_id {payload.get('run_id')!r} != pre-registration {run_id!r}")

    binding = (payload.get("toolchain") or {}).get("opencode")
    if not isinstance(binding, dict):
        raise GuardError("receipt has no toolchain.opencode binding")

    expected = {
        "config.template_sha256": (binding["config"]["template_sha256"],
                                   prereg["config"]["template_sha256"]),
        "config.materialized_sha256": (binding["config"]["materialized_sha256"],
                                       prereg["config"]["materialized_sha256"]),
        "prompt_sha256": (binding["prompt_sha256"], prereg["prompt_sha256"]),
        "binary.sha256": (binding["binary"]["sha256"], prereg["binary"]["sha256"]),
    }
    for label, (found, wanted) in expected.items():
        if found != wanted:
            raise GuardError(f"binding {label} {found!r} != pre-registered {wanted!r}")

    if binding["model"] != prereg["model"]:
        raise GuardError(f"binding model {binding['model']!r} != pre-registered {prereg['model']!r}")
    if payload["agent"]["model"] != binding["model"]:
        raise GuardError("the recorded agent model disagrees with the binding")

    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        found = (payload.get("corpus") or {}).get(set_name)
        wanted = (prereg.get("corpus") or {}).get(set_name)
        if not found or not wanted:
            raise GuardError(f"receipt/pre-registration lack the {set_name} corpus manifest")
        if found.get("sha256") != wanted.get("sha256") or found.get("count") != wanted.get("count"):
            raise GuardError(
                f"the {set_name} corpus manifest changed after pre-registration")


def _rematerialize(repo_root: Path, prereg: dict, binding: dict) -> None:
    relative = (prereg.get("config") or {}).get(
        "template_path", "oracle/slice/opencode_config/opencode.json.template")
    template = (repo_root / relative).read_text(encoding="utf-8")
    rendered = opencode.materialize(template, prereg["substitutions"])
    digest = opencode._sha256(rendered.encode("utf-8"))
    if digest != binding["config"]["materialized_sha256"]:
        raise GuardError(
            "the recorded configuration digest is not reproducible from the tracked "
            f"template and the pre-registered substitutions ({digest})")


def _check_pinned_sources(repo_root: Path, prereg: dict, payload: dict) -> None:
    """Refuse to replay when the oracle no longer matches what was pinned."""
    toolchain = payload.get("toolchain") or {}
    for key, rel in (("harness_sha256", "oracle/slice/harness.py"),
                     ("comparator_sha256", "oracle/slice/compare.py"),
                     ("driver_sha256", "oracle/slice/driver.c"),
                     ("interface_sha256", "oracle/slice/synth.h")):
        current = receipts.file_sha256(repo_root / rel)
        if current != toolchain.get(key):
            raise GuardError(f"{rel} changed since the pinned run; refusing to replay")
    bridge = prereg.get("source_hashes", {}).get("bridge_sha256")
    if bridge and bridge != receipts.file_sha256(repo_root / "oracle/slice/bridge.py"):
        raise GuardError("oracle/slice/bridge.py changed since the pinned run; refusing to replay")


def _replay(repo_root: Path, artifact: Path, run_id: str) -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        build = harness.build(artifact, Path(tmp) / "build", cc="cc")
        verdicts = {}
        for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
            fixtures = corpus.load_corpus(set_name, root=repo_root / "oracle/slice/fixtures")
            diagnostics = [harness.evaluate(build, fixture, run_id=run_id,
                                            timeout_s=harness.DEFAULT_TIMEOUT_S)
                           for fixture in fixtures]
            verdicts[set_name] = compare.verdict(diagnostics)
    return verdicts


def verify_run(repo_root: Path, prereg_path: Path) -> str:
    prereg = _load(prereg_path)
    run_id = prereg.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise GuardError(f"{prereg_path} has no run_id")

    receipt_path = repo_root / RECEIPTS_DIR / f"{run_id}.json"
    if not receipt_path.is_file():
        raise PendingReceipt(f"no published receipt for {run_id}")
    receipt = _load(receipt_path)
    receipts.verify(receipt, expected_sha256=receipt.get("sha256"))

    payload = receipt["payload"]
    if payload.get("verdict", {}).get("status") != "certified":
        raise GuardError(f"published receipt {run_id} is not certified")

    _cross_check(receipt, prereg)
    _rematerialize(repo_root, prereg, payload["toolchain"]["opencode"])

    # Undeclared absolute paths: scope the scan to the identities the *harness*
    # authors. Free-form strings the child produced (attempt notes, denial
    # resources, error text) are necessarily arbitrary, and flagging them would
    # fail a run merely for an agent naming a path; the denials are separately
    # required to be structured. Everything here is built by the controller, the
    # adapter or the corpus loader.
    declared = tuple(str(prereg[key]).rstrip("/") + "/"
                     for key in ("staging_root", "repo_root") if prereg.get(key))
    allow = SYSTEM_PREFIXES + declared
    harness_authored = {key: payload.get(key)
                        for key in ("toolchain", "agent", "corpus", "source", "verdict")}
    stray = check_absolute_paths(json.dumps(harness_authored, sort_keys=True), allow)
    if stray:
        raise GuardError("undeclared absolute paths in the bound identities: "
                         + ", ".join(stray))

    # Leak scan over the *published* summary only; the receipt legitimately names
    # held-out fixtures in its corpus manifest.
    summary_path = repo_root / AGENTS_DIR / f"{run_id}.events.json"
    if not summary_path.is_file():
        raise GuardError(f"missing published event summary: {summary_path}")
    summary_text = summary_path.read_text(encoding="utf-8")
    hits = opencode.scan_for_leaks(summary_text, opencode.held_out_fingerprints())
    if hits:
        raise GuardError("held-out fingerprints in the published event summary: "
                         + ", ".join(hits))

    # The published summary must be the one the receipt sealed.
    summary = json.loads(summary_text)
    if opencode._sha256(canonical_bytes(summary)) != payload["toolchain"]["opencode"]["events"]["summary_sha256"]:
        raise GuardError("the published event summary does not match the sealed digest")

    _check_pinned_sources(repo_root, prereg, payload)

    artifact = repo_root / ARTIFACTS_DIR / f"{payload['source']['final_candidate_sha256']}.c"
    if not artifact.is_file():
        raise GuardError(f"missing committed artifact: {artifact}")
    if receipts.file_sha256(artifact) != payload["source"]["final_candidate_sha256"]:
        raise GuardError("the committed artifact does not match the sealed candidate digest")

    verdicts = _replay(repo_root, artifact, run_id)
    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        if verdicts[set_name]["status"] != compare.STATUS_PASS:
            raise GuardError(f"replay failed on the {set_name} corpus: {verdicts[set_name]['status']}")
    return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_oracle_receipt")
    parser.add_argument("--root", default=str(_REPO_ROOT))
    parser.add_argument("--require-receipts", action="store_true",
                        help="fail when a pre-registration has no published receipt")
    args = parser.parse_args(argv)
    repo_root = Path(args.root).resolve()

    prereg_dir = repo_root / PREREGISTRATION_DIR
    prereg_files = sorted(prereg_dir.glob("*.json")) if prereg_dir.is_dir() else []
    if not prereg_files:
        print("oracle receipts: no published pre-registration yet (nothing to verify)")
        return 0

    verified: list[str] = []
    pending: list[str] = []
    for prereg_path in prereg_files:
        try:
            verified.append(verify_run(repo_root, prereg_path))
        except PendingReceipt as exc:
            pending.append(prereg_path.stem)
            print(f"oracle receipts: PENDING {prereg_path.name}: {exc}")
        except GuardError as exc:
            print(f"oracle receipts: FAILED {prereg_path.name}: {exc}", file=sys.stderr)
            return 1
        except (harness.HarnessError, receipts.ReceiptError, OSError) as exc:
            print(f"oracle receipts: FAILED {prereg_path.name}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1

    if pending and args.require_receipts:
        print(f"oracle receipts: {len(pending)} pre-registration(s) without a receipt",
              file=sys.stderr)
        return 1
    summary = f"oracle receipts: verified {len(verified)} published run(s)"
    if verified:
        summary += f": {', '.join(verified)}"
    if pending:
        summary += f"; {len(pending)} declared and pending: {', '.join(pending)}"
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
