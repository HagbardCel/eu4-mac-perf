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
3. **checks the execution contract** - both candidate identities exist and
   differ, the baseline commit is the pinned revision, and the declared bounds
   agree with what the receipt recorded and with what actually happened;
4. **verifies every pin against the pinned revision**, not against the working
   tree: each recorded identity must be the digest of the file at that revision
   (``git cat-file blob <rev>:<path>``). A change to the current sources cannot
   invalidate - or rescue - a historical claim, and this guard cannot reject its
   own later remediation;
5. **re-materializes the agent configuration** from the tracked template plus the
   pre-registered substitution map, and requires the recorded digest to match;
6. **replays both corpora over the committed artifact** in a *fresh interpreter
   whose import path is rooted in the pinned revision*, after verifying the
   module file locations and source digests inside that interpreter. The
   artifact is read from outside the pinned tree. The replay's verdict and the
   manifests it computed must both agree with the receipt;
7. **detects corpus drift** - the fixtures in the current tree must still recompute
   to the manifests this run was evaluated against, so a coordinated edit to an
   input and its expectation cannot pass unnoticed;
8. **reconciles the call census** - when the receipt seals a tool-call journal the
   published journal is re-checked and re-reconciled against the agent-visible
   event census; when it does not (a run that predates the journal) the guard
   *reports* that the reconciliation is aggregate-only rather than implying it;
9. **scans the harness-authored identities for undeclared absolute paths** and
   **scans the published, sanitized event summary** for held-out fingerprints.

It exits non-zero on the first violation. A pre-registration whose receipt is not
published yet is reported as **pending** rather than failed, because the
declaration is deliberately committed before the run; ``--require-receipts``
turns that into a failure. A *receipt* with no declaration is always a failure -
otherwise an undeclared run would be published unverified.

Provenance is reported per run, and the categories are kept distinct: the
original pre-registration (declared before execution), the sealed receipt
(recorded during execution), git history (what a revision contained), a
retrospective identity pin (post-run reconstruction) and any supplementary
trusted-side record published afterwards. A retrospective pin never acquires the
standing of an execution-time attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
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
#: Retrospective pins live in a *subdirectory*: the declaration set is the
#: non-recursive ``preregistration/*.json`` glob below, so a post-run
#: reconstruction can never be mistaken for a pre-run declaration.
RETROSPECTIVE_DIR = PREREGISTRATION_DIR / "retrospective"

#: The files the replay's semantics come from. The guard verifies these against
#: the pinned revision *inside* the replay interpreter before evaluating a
#: fixture, so the verdict can only be produced by the pinned code.
REPLAY_SOURCES = ("oracle/core.py", "oracle/slice/harness.py",
                  "oracle/slice/compare.py", "oracle/slice/corpus.py")

#: Every identity a run may pin, mapped to its repository path. The keys are the
#: ones ``oracle.slice.opencode_run.SOURCE_PATHS`` emits, plus the four the
#: receipt has always carried directly in ``toolchain``.
PIN_PATHS = {
    "harness_sha256": "oracle/slice/harness.py",
    "comparator_sha256": "oracle/slice/compare.py",
    "driver_sha256": "oracle/slice/driver.c",
    "interface_sha256": "oracle/slice/synth.h",
    "bridge_sha256": "oracle/slice/bridge.py",
    "controller_sha256": "oracle/slice/controller.py",
    "adapters_sha256": "oracle/slice/adapters.py",
    "opencode_sha256": "oracle/slice/opencode.py",
    "core_sha256": "oracle/core.py",
    "corpus_sha256": "oracle/slice/corpus.py",
    "receipts_sha256": "oracle/slice/receipts.py",
    "launch_driver_sha256": "oracle/slice/opencode_run.py",
}

#: The receipt's own ``toolchain`` block carries these four execution-time
#: identities directly, whatever the declaration pins.
TOOLCHAIN_PINS = ("harness_sha256", "comparator_sha256", "driver_sha256", "interface_sha256")

#: Absolute paths that may legitimately appear in a published receipt: system
#: locations named by the compiler probe, plus the run's declared staging root.
SYSTEM_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/etc/", "/opt/homebrew/", "/usr/local/",
    "/Applications/", "/System/", "/Library/", "/private/var/folders/",
    "/private/tmp/", "/tmp/", "/var/folders/",
)

#: Limitations a retrospective pin may declare about a run that predates the
#: contract. Each names a check the guard *cannot* perform from execution-time
#: evidence; declaring it is what lets the weaker reading be published without
#: being presented as the stronger one.
LIMIT_BASELINE_COMMIT = "baseline-commit"
LIMIT_ACCEPTANCE_GATES = "acceptance-gates"
LIMIT_CALL_JOURNAL = "call-journal"
KNOWN_LIMITS = (LIMIT_BASELINE_COMMIT, LIMIT_ACCEPTANCE_GATES, LIMIT_CALL_JOURNAL)

#: Run in a fresh interpreter rooted in the pinned revision. It verifies its own
#: module provenance and the pinned source digests *before* loading a fixture,
#: then reports the verdicts and the manifests it computed.
_REPLAY_PROGRAM = r'''
import hashlib
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
artifact = Path(sys.argv[2]).resolve()
run_id = sys.argv[3]
expected = json.loads(sys.argv[4])
sys.path.insert(0, str(root))

from oracle.slice import compare, corpus, harness  # noqa: E402

problems = []
for relative, wanted in sorted(expected["sources"].items()):
    found = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    if found != wanted:
        problems.append(f"{relative} in the pinned tree is {found}, not {wanted}")
modules = {}
for module in (compare, corpus, harness):
    origin = Path(module.__file__).resolve()
    modules[module.__name__] = str(origin)
    if not origin.is_relative_to(root):
        problems.append(f"{module.__name__} was imported from {origin}, outside the pinned tree")
if problems:
    print(json.dumps({"problems": problems}))
    raise SystemExit(0)

with tempfile.TemporaryDirectory() as scratch:
    build = harness.build(artifact, Path(scratch) / "build")
    verdicts = {}
    manifests = {}
    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        fixtures = corpus.load_corpus(set_name, root=root / "oracle" / "slice" / "fixtures")
        manifests[set_name] = corpus.manifest(fixtures, set_name=set_name)
        diagnostics = [harness.evaluate(build, fixture, run_id=run_id,
                                        timeout_s=harness.DEFAULT_TIMEOUT_S)
                       for fixture in fixtures]
        verdicts[set_name] = compare.verdict(diagnostics)
print(json.dumps({"problems": [], "verdicts": verdicts, "manifests": manifests,
                  "modules": modules}))
'''

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


# --- plumbing -----------------------------------------------------------------


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GuardError(f"{path} is not a JSON object")
    return data


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest(Path(path).read_bytes())


def _git(database: Path, *arguments: str) -> bytes:
    probe = subprocess.run(["git", "-C", str(database), *arguments], capture_output=True)
    if probe.returncode != 0:
        raise GuardError(
            f"git {' '.join(arguments)} failed in {database}: "
            f"{probe.stderr.decode('utf-8', 'replace').strip()}")
    return probe.stdout


def _revision_db(repo_root: Path) -> Path | None:
    """The repository whose revision database describes the pinned run."""
    for candidate in (repo_root, _REPO_ROOT):
        if (candidate / ".git").exists():
            return candidate
    return None


def _blob_digest(database: Path, revision: str, relative: str) -> str:
    return _digest(_git(database, "cat-file", "blob", f"{revision}:{relative}"))


def check_absolute_paths(serialized: str, allow_prefixes: tuple[str, ...]) -> list[str]:
    stray = []
    for path in sorted(set(_absolute_regex().findall(serialized))):
        if not any(path.startswith(prefix) for prefix in allow_prefixes):
            stray.append(path)
    return stray


# --- cross-checks -------------------------------------------------------------


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


def _execution_contract(receipt: dict, prereg: dict, revision: str,
                        limits: set[str]) -> None:
    """Both candidate identities, the pinned revision and the declared bounds."""
    payload = receipt.get("payload") or {}
    source = payload.get("source") or {}
    final = source.get("final_candidate_sha256")
    defective = source.get("defective_candidate_sha256")
    if not final or not defective:
        raise GuardError("the receipt does not record both candidate identities")
    if final == defective:
        raise GuardError("the accepted candidate is byte-identical to the defective input")
    pinned = source.get("baseline_commit")
    if pinned is None:
        # A dimmer but honest reading: the revision comes from a post-run
        # reconstruction, which must say so. It never becomes an execution-time
        # record.
        if LIMIT_BASELINE_COMMIT not in limits:
            raise GuardError(
                "the receipt records no baseline commit and no retrospective pin declares "
                "that limitation")
    elif pinned != revision:
        raise GuardError(
            f"the receipt's baseline commit {pinned!r} is not the pinned revision {revision!r}")
    declared_source = prereg.get("source") or {}
    if declared_source:
        if declared_source.get("defective_candidate_sha256") not in (None, defective):
            raise GuardError("the defective candidate in the receipt is not the declared input")
        if declared_source.get("baseline_commit") not in (None, pinned):
            raise GuardError("the receipt's baseline commit is not the declared one")

    execution = payload.get("execution") or {}
    declared_run = prereg.get("run") or {}
    limits = execution.get("limits")
    if isinstance(limits, dict):
        for key in ("budget_calls", "wall_seconds", "finalization_seconds"):
            if key in declared_run and limits.get(key) != declared_run[key]:
                raise GuardError(
                    f"execution.limits.{key} {limits.get(key)!r} != declared {declared_run[key]!r}")
    attempts = execution.get("attempts") or []
    if not attempts:
        raise GuardError("the run records no attempt")
    allowed = declared_run.get("attempts", limits.get("max_attempts") if isinstance(limits, dict)
                               else None)
    if isinstance(allowed, int) and len(attempts) > allowed:
        raise GuardError(f"the run took {len(attempts)} attempts, more than the declared {allowed}")
    declared_wall = declared_run.get("wall_seconds")
    wall = execution.get("wall_seconds")
    if isinstance(declared_wall, (int, float)) and isinstance(wall, (int, float)) \
            and wall > declared_wall:
        raise GuardError(f"the run lasted {wall}s, beyond the declared {declared_wall}s")
    finalization = execution.get("finalization") or {}
    declared_finalization = declared_run.get("finalization_seconds")
    duration = finalization.get("duration_s")
    if isinstance(declared_finalization, (int, float)) and isinstance(duration, (int, float)) \
            and duration > declared_finalization:
        raise GuardError(
            f"finalization lasted {duration}s, beyond the declared {declared_finalization}s")
    if isinstance(declared_finalization, (int, float)) \
            and finalization.get("budget_seconds") not in (None, declared_finalization):
        raise GuardError("the finalization budget in the receipt is not the declared one")


def _pins(receipt: dict, prereg: dict, retro: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """Merge declared pins; return ``(pins, unauthenticated)``.

    A pin may come from the pre-registration (declared before execution) or from
    a retrospective pin (reconstructed afterwards). Where both name the same
    identity they must agree: a reconstruction cannot quietly disagree with the
    declaration it supplements.
    """
    pins: dict[str, str] = {}
    for label, table in (("pre-registration", prereg.get("source_hashes") or {}),
                         ("retrospective pin", (retro or {}).get("source_hashes") or {})):
        for key, value in table.items():
            if key in PIN_PATHS:
                if key in pins and pins[key] != value:
                    raise GuardError(
                        f"the {label} pins {key}={value!r} but it was already pinned as {pins[key]!r}")
                pins[key] = value
    toolchain = (receipt.get("payload") or {}).get("toolchain") or {}
    for key in TOOLCHAIN_PINS:
        value = toolchain.get(key)
        if value is None:
            raise GuardError(f"the receipt does not record {key}")
        if key in pins and pins[key] != value:
            raise GuardError(
                f"the receipt records {key}={value!r} but it was pinned as {pins[key]!r}")
        pins[key] = value
    unauthenticated = dict((retro or {}).get("unauthenticated") or {})
    return pins, unauthenticated


def _check_pins_against_revision(database: Path, revision: str, pins: dict[str, str],
                                 unauthenticated: dict[str, str]) -> list[str]:
    """Every pin must be the digest of the file *at that revision*."""
    notes: list[str] = []
    for key, value in sorted(pins.items()):
        relative = PIN_PATHS[key]
        if relative in unauthenticated:
            notes.append(f"{relative}: {unauthenticated[relative]}")
            continue
        found = _blob_digest(database, revision, relative)
        if found != value:
            raise GuardError(
                f"{relative} at {revision} is {found}, but the run pinned {value}")
    return notes


def _replay(repo_root: Path, database: Path, revision: str, artifact: Path, run_id: str) -> dict:
    """Re-evaluate the artifact with the pinned revision's own modules.

    The revision is materialized with ``git archive`` (read-only on the
    repository) and the evaluation runs in a fresh interpreter whose import path
    is rooted there: a module already imported by this process cannot be reused,
    and the interpreter refuses to evaluate anything until it has verified the
    pinned source digests and its own module locations.
    """
    sources = {relative: _blob_digest(database, revision, relative) for relative in REPLAY_SOURCES}
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "pinned"
        tree.mkdir(parents=True)
        archive = _git(database, "archive", "--format=tar", revision)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            try:
                tar.extractall(tree, filter="data")
            except TypeError:  # pragma: no cover - Python < 3.12
                tar.extractall(tree)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(tree)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        probe = subprocess.run(
            [sys.executable, "-c", _REPLAY_PROGRAM, str(tree), str(artifact), run_id,
             json.dumps({"sources": sources})],
            cwd=str(tree), env=environment, capture_output=True, text=True)
    if probe.returncode != 0:
        raise GuardError(f"the pinned replay failed to run: {probe.stderr.strip()[-400:]}")
    text = probe.stdout.strip().splitlines()[-1:] or [""]
    try:
        reported = json.loads(text[0])
    except json.JSONDecodeError as exc:
        raise GuardError(f"the pinned replay produced no verdict: {probe.stdout[:200]!r}") from exc
    if reported.get("problems"):
        raise GuardError("the pinned replay refused to evaluate: "
                         + "; ".join(reported["problems"]))
    return reported


def _check_corpus_drift(repo_root: Path, payload: dict) -> None:
    """The current fixtures must still be the ones this run was evaluated against."""
    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        try:
            current = corpus.manifest(
                corpus.load_corpus(set_name, root=repo_root / "oracle" / "slice" / "fixtures"),
                set_name=set_name)
        except (corpus.CorpusError, OSError) as exc:
            raise GuardError(f"cannot recompute the {set_name} manifest: {exc}") from exc
        declared = (payload.get("corpus") or {}).get(set_name)
        if not declared or current.get("sha256") != declared.get("sha256"):
            raise GuardError(
                f"the {set_name} corpus in the current tree is not the one this run was "
                "evaluated against; the committed fixtures are frozen ground truth")


def _check_committed_declaration(repo_root: Path, run_id: str, commit: str,
                                 relative: str) -> str:
    """The published declaration must be the Git object at the run's commit.

    This re-verifies, from published evidence, that the pre-registration the run
    bound was a *committed* object at the checkout that ran - not merely a readable
    file. It is the strongest statement an offline check can make; it does not
    prove the commit was pushed to a remote before execution.
    """
    database = _revision_db(repo_root)
    if database is None:
        raise GuardError("no revision database is available to check the declaration commit")
    blob = _git(database, "cat-file", "blob", f"{commit}:{relative}")
    run_time_file = repo_root / relative
    if not run_time_file.is_file() or run_time_file.read_bytes() != blob:
        raise GuardError(
            f"the pre-registration at {relative} is not the committed declaration {commit}")
    published = repo_root / PREREGISTRATION_DIR / f"{run_id}.json"
    if published.read_bytes() != blob:
        raise GuardError(
            f"the published pre-registration {run_id}.json is not the committed declaration")
    return f"declaration: committed at {commit[:12]} ({relative})"


def _check_acceptance(repo_root: Path, run_id: str, payload: dict, prereg: dict,
                      limits: set[str]) -> list[str]:
    """The repair-acceptance evidence, re-checked from what was published.

    A run that recorded the contract is held to it. A run that predates it - the
    original P3-B evaluation - can still be published, but only with a
    retrospective pin that *names* the limitation: the guard must not present a
    weaker reading as if it were the stronger one, and it must not quietly accept
    an acceptance run that simply never recorded its gates.
    """
    notes: list[str] = []
    acceptance = payload.get("acceptance")
    recorded = isinstance(acceptance, dict) and bool(acceptance.get("required"))
    acceptance_run = prereg.get("mode") == "acceptance"

    if recorded:
        failed = acceptance.get("failed") or []
        if failed:
            raise GuardError(f"the published acceptance gates did not pass: {', '.join(failed)}")
        for name, gate in sorted((acceptance.get("gates") or {}).items()):
            if not gate.get("ok"):
                raise GuardError(f"the published acceptance gate {name!r} did not pass: {gate}")
        notes.append("acceptance gates: enforced at execution and re-checked here")
    elif acceptance_run:
        if LIMIT_ACCEPTANCE_GATES not in limits:
            raise GuardError(
                "this acceptance run does not record its repair-acceptance gates, and no "
                "retrospective pin declares that limitation")
        notes.append("acceptance gates: NOT recorded at execution; the retrospective pin "
                     "declares the limitation (the run predates the repair contract)")

    if recorded:
        source = payload.get("source") or {}
        declaration_commit = source.get("declaration_commit")
        preregistration_path = source.get("preregistration_path")
        if not declaration_commit or not preregistration_path:
            raise GuardError(
                "a repair-acceptance receipt must record the commit and path of the "
                "pre-registration it ran under")
        notes.append(_check_committed_declaration(
            repo_root, run_id, declaration_commit, preregistration_path))

    journal = (payload.get("agent") or {}).get("journal")
    if isinstance(journal, dict):
        path = repo_root / AGENTS_DIR / f"{run_id}.tool-calls.json"
        if not path.is_file():
            raise GuardError(f"the receipt seals a call journal but {path.name} is not published")
        text = path.read_text(encoding="utf-8")
        if _digest(text.encode("utf-8")) != journal.get("sha256"):
            raise GuardError("the published call journal does not match the sealed digest")
        rows = json.loads(text).get("calls") or []
        result = _reconcile((payload.get("toolchain") or {}).get("opencode"), rows, payload)
        if not result["ok"]:
            raise GuardError("the published call census does not reconcile: "
                             + "; ".join(result["problems"]))
        notes.append(
            f"call census: reconciled from the journal "
            f"({result['admitted']} admitted, {result['rejected_before_admission']} rejected "
            f"before admission, {result['denied']} permission-denied)")
    elif recorded:
        raise GuardError("the repair-acceptance contract requires a sealed call journal")
    elif acceptance_run:
        if LIMIT_CALL_JOURNAL not in limits:
            raise GuardError("no call journal was published and no retrospective pin says why")
        granted = ((payload.get("agent") or {}).get("budget") or {}).get("granted")
        completed = ((payload.get("toolchain") or {}).get("opencode") or {}).get(
            "events", {}).get("tool_calls")
        notes.append(
            f"call census: the per-tool journal is not verifiable (the run predates it); the "
            f"aggregate stands at {granted} admitted = {completed} completed")

    record_path = repo_root / AGENTS_DIR / f"{run_id}.tests.json"
    if record_path.is_file():
        record = _load(record_path)
        final = (payload.get("source") or {}).get("final_candidate_sha256")
        if record.get("candidate_sha256") != final:
            raise GuardError("the supplementary test record does not name the sealed candidate")
        if (record.get("summary") or {}).get("status") != compare.STATUS_PASS:
            raise GuardError("the supplementary test record is not a PASS")
        notes.append(
            "supplementary test record: published after the run; its hash is the pre-build "
            "candidate hash, corroborating (not attesting) the sealed identity")
    return notes


def _reconcile(binding: dict | None, rows: list[dict], payload: dict) -> dict:
    """Re-run the execution-side reconciliation over published evidence."""
    from oracle.slice.controller import _reconcile_calls
    events = (binding or {}).get("events") or {}
    granted = ((payload.get("agent") or {}).get("budget") or {}).get("granted")
    return _reconcile_calls(binding, rows, granted, events.get("oracle_invocations"))


def _declared_limits(retro: dict | None) -> set[str]:
    """The limitations a retrospective pin declares, validated against the known set."""
    limits: set[str] = set()
    for entry in (retro or {}).get("limits") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("check"), str):
            raise GuardError("every retrospective limit must name a check")
        name = entry["check"]
        if name not in KNOWN_LIMITS:
            raise GuardError(f"unknown retrospective limit {name!r}")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise GuardError(f"retrospective limit {name!r} must state a reason")
        limits.add(name)
    return limits


def _retrospective_pin(repo_root: Path, run_id: str) -> dict | None:
    path = repo_root / RETROSPECTIVE_DIR / f"{run_id}.json"
    if not path.is_file():
        return None
    pin = _load(path)
    if pin.get("kind") != "retrospective-identity-pin":
        raise GuardError(f"{path} is not a retrospective identity pin")
    if pin.get("run_id") != run_id:
        raise GuardError(f"{path} names run {pin.get('run_id')!r}, not {run_id!r}")
    return pin


def verify_run(repo_root: Path, prereg_path: Path) -> tuple[str, list[str]]:
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
    finalization_error = ((payload.get("execution") or {}).get("finalization") or {}).get("error")
    if finalization_error:
        # Certification and a finalization fault are mutually exclusive: a run whose
        # mandatory evidence could not be published is aborted at execution, so a
        # certified receipt that records one is inconsistent.
        raise GuardError(
            f"published receipt {run_id} is certified but records a finalization "
            f"error: {finalization_error}")

    notes: list[str] = []
    _cross_check(receipt, prereg)
    _rematerialize(repo_root, prereg, payload["toolchain"]["opencode"])

    retro = _retrospective_pin(repo_root, run_id)
    limits = _declared_limits(retro)
    source = payload.get("source") or {}
    revision = source.get("baseline_commit") or (retro or {}).get("pre_image_commit")
    if not isinstance(revision, str) or not revision:
        raise GuardError(
            f"no source revision is pinned for {run_id}: neither the receipt nor a "
            "retrospective pin names one, so historical provenance cannot be checked")
    database = _revision_db(repo_root)
    if database is None:
        raise GuardError("no revision database is available to resolve the pinned revision")
    resolved = _git(database, "rev-parse", f"{revision}^{{commit}}").decode("utf-8").strip()
    revision = resolved
    if source.get("baseline_commit"):
        notes.append(f"provenance: revision {revision[:12]} recorded at execution time")
    else:
        notes.append(f"provenance: revision {revision[:12]} from the retrospective pin "
                     "(not recorded at execution time)")

    _execution_contract(receipt, prereg, revision, limits)

    snapshot = source.get("snapshot")
    if isinstance(snapshot, str) and snapshot.startswith("/"):
        notes.append("snapshot path: recorded absolute (the receipt predates the "
                     "relative-path policy)")

    pins, unauthenticated = _pins(receipt, prereg, retro)
    notes.extend(_check_pins_against_revision(database, revision, pins, unauthenticated))
    notes.append(f"pins: {len(pins)} identit(ies) verified against the revision, "
                 f"{len(unauthenticated)} recorded as unauthenticated")

    # Undeclared absolute paths: scope the scan to the identities the *harness*
    # authors. Free-form strings the child produced (attempt notes, denial
    # resources, error text) are necessarily arbitrary, and flagging them would
    # fail a run merely for an agent naming a path; the denials are separately
    # required to be structured.
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
    if opencode._sha256(canonical_bytes(summary)) \
            != payload["toolchain"]["opencode"]["events"]["summary_sha256"]:
        raise GuardError("the published event summary does not match the sealed digest")

    _check_corpus_drift(repo_root, payload)
    notes.append("corpus: the current fixtures still match the manifests this run bound")

    notes.extend(_check_acceptance(repo_root, run_id, payload, prereg, limits))

    artifact = repo_root / ARTIFACTS_DIR / f"{payload['source']['final_candidate_sha256']}.c"
    if not artifact.is_file():
        raise GuardError(f"missing committed artifact: {artifact}")
    if _digest_file(artifact) != payload["source"]["final_candidate_sha256"]:
        raise GuardError("the committed artifact does not match the sealed candidate digest")

    reported = _replay(repo_root, database, revision, artifact.resolve(), run_id)
    for set_name in (corpus.VISIBLE, corpus.HELD_OUT):
        verdict = reported["verdicts"][set_name]
        if verdict["status"] != compare.STATUS_PASS:
            raise GuardError(f"replay failed on the {set_name} corpus: {verdict['status']}")
        declared_manifest = (payload.get("corpus") or {}).get(set_name)
        if reported["manifests"][set_name].get("sha256") != (declared_manifest or {}).get("sha256"):
            raise GuardError(
                f"the {set_name} corpus at the pinned revision is not the one the receipt bound")
    notes.append(f"replay: both corpora PASS under the pinned revision's own modules "
                 f"({len(reported['modules'])} verified)")
    return run_id, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_oracle_receipt")
    parser.add_argument("--root", default=str(_REPO_ROOT))
    parser.add_argument("--require-receipts", action="store_true",
                        help="fail when a pre-registration has no published receipt")
    args = parser.parse_args(argv)
    repo_root = Path(args.root).resolve()

    prereg_dir = repo_root / PREREGISTRATION_DIR
    # Non-recursive: a retrospective pin lives in a subdirectory and is never part
    # of the declaration set.
    prereg_files = sorted(prereg_dir.glob("*.json")) if prereg_dir.is_dir() else []

    receipts_dir = repo_root / RECEIPTS_DIR
    published = sorted(path.stem for path in receipts_dir.glob("*.json")) \
        if receipts_dir.is_dir() else []
    declared = {path.stem for path in prereg_files}
    undeclared = [name for name in published if name not in declared]
    if undeclared:
        print("oracle receipts: FAILED a published receipt has no pre-registration: "
              + ", ".join(undeclared), file=sys.stderr)
        return 1

    if not prereg_files:
        message = "oracle receipts: no published pre-registration yet (nothing to verify)"
        if published:
            print(message, file=sys.stderr)
            return 1
        print(message)
        return 0

    verified: list[tuple[str, list[str]]] = []
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
    for run_id, notes in verified:
        print(f"oracle receipts: {run_id}")
        for note in notes:
            print(f"  - {note}")
    summary = f"oracle receipts: verified {len(verified)} published run(s)"
    if verified:
        summary += f": {', '.join(run_id for run_id, _ in verified)}"
    if pending:
        summary += f"; {len(pending)} declared and pending: {', '.join(pending)}"
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
