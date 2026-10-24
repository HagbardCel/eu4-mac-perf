"""Function-package descriptors for the G3a adapter seam.

A *function package* is the boundary object the seam drives: one function id, the
defective candidate source, the public header, the visible fixture root and the
prompt/configuration material of the pinned agent binding. The seam does not
interpret a function's semantics; it only describes where the certified P3
primitives can find the material.

G3a has exactly one package, ``synth:apply``, which is the existing synthetic P3
target. Anything else is refused explicitly with ``refused: no-package`` rather
than being guessed at. This is deliberate: the P3 workspace, harness and
comparator are specialised to ``candidate.c`` / ``synth.h`` and to the synthetic
observable contract. A real reconstructed C++ function needs G3b's workspace and
per-function comparator, which this seam does not claim to provide.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oracle.function_agent import catalog
from oracle.slice import corpus, harness

#: The only package this seam knows how to describe.
SYNTH_APPLY = "synth:apply"

SUPPORTED_FUNCTION_IDS: tuple[str, ...] = (SYNTH_APPLY,)

#: The agent-visible tool vocabulary. Imported by name into the descriptor so a
#: package cannot silently widen what the agent may call.


class PackageRefused(RuntimeError):
    """A function package is missing, incomplete or unsupported.

    ``status`` is the honest machine-readable outcome the seam returns. It is
    never a success value, and a refused package never starts a controller run.
    """

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(f"refused: {reason}: {detail}")

    @property
    def status(self) -> str:
        return f"refused: {self.reason}"

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class FunctionPackage:
    """Everything the seam needs to drive one function through P3.

    The descriptor is deliberately data-only. It names trusted material; it does
    not carry expectations, held-out fixtures or a controller token.
    """

    function_id: str
    candidate_src: Path
    header_src: Path
    fixtures_root: Path
    prompt_path: Path
    config_template_dir: Path
    tool_names: tuple[str, ...]
    evidence_tier: str = "synthetic-portable-c"
    #: The P3 workspace is candidate-only and specialised to ``candidate.c`` and
    #: ``synth.h``. Both flags are recorded so no caller can mistake this seam for
    #: a general reconstructed-C++ function workspace.
    workspace_kind: str = "synthetic-candidate-only"
    arbitrary_cpp_supported: bool = False
    generic_workspace: bool = False

    def describe(self) -> dict[str, object]:
        """A publishable description of what was actually bound."""
        return {
            "function_id": self.function_id,
            "candidate": self.candidate_src.name,
            "header": self.header_src.name,
            "visible_fixture_root": self.fixtures_root.name,
            "tool_names": list(self.tool_names),
            "evidence_tier": self.evidence_tier,
            "workspace_kind": self.workspace_kind,
            "arbitrary_cpp_supported": self.arbitrary_cpp_supported,
            "generic_workspace": self.generic_workspace,
        }


def supported_packages() -> tuple[str, ...]:
    """The function ids this seam can describe."""
    return SUPPORTED_FUNCTION_IDS


def _default_fixtures_root() -> Path:
    return corpus.FIXTURES_DIR


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise PackageRefused("incomplete-package", f"{what} is missing: {path}")


def _require_visible_corpus(root: Path, function_id: str) -> None:
    visible = root / corpus.VISIBLE
    if not visible.is_dir():
        raise PackageRefused("incomplete-package",
                             f"visible fixture directory is missing: {visible}")
    try:
        fixtures = corpus.load_corpus(corpus.VISIBLE, root=root)
    except corpus.CorpusError as exc:
        raise PackageRefused("incomplete-package",
                             f"visible corpus cannot be loaded: {exc}") from exc
    ids = {fixture.function_id for fixture in fixtures}
    if function_id not in ids:
        raise PackageRefused(
            "incomplete-package",
            f"the visible corpus contains no fixtures for {function_id!r}")


def describe_package(function_id: str, *, fixtures_root: Path | None = None) -> FunctionPackage:
    """Describe one supported function package, or refuse it.

    Raises :class:`PackageRefused` with ``reason == "no-package"`` for any id the
    seam does not support and ``reason == "incomplete-package"`` when the declared
    material is absent or unusable.
    """
    if function_id not in SUPPORTED_FUNCTION_IDS:
        raise PackageRefused(
            "no-package",
            f"{function_id!r} is not a supported function package; supported: "
            + ", ".join(SUPPORTED_FUNCTION_IDS))
    # The package is supported, so the trusted material must exist. This is still
    # a refusal, not a run: a half-materialized package must not become a verdict.
    catalog.validate_catalog()
    root = Path(fixtures_root) if fixtures_root else _default_fixtures_root()
    candidate = harness.SLICE_DIR / "target.c"
    header = harness.SLICE_DIR / "synth.h"
    config_dir = harness.SLICE_DIR / "opencode_config"
    _require_file(candidate, "defective candidate source")
    _require_file(header, "public interface")
    _require_file(config_dir / "opencode.json.template", "pinned configuration template")
    _require_file(config_dir / "prompt.txt", "pinned repair prompt")
    _require_visible_corpus(root, function_id)
    return FunctionPackage(
        function_id=function_id,
        candidate_src=candidate,
        header_src=header,
        fixtures_root=root,
        prompt_path=config_dir / "prompt.txt",
        config_template_dir=config_dir,
        tool_names=catalog.AGENT_VISIBLE_TOOLS,
    )
