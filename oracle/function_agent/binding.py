"""Pinned OpenCode binding reuse for a function package.

This module does not launch anything. It builds the existing
:class:`oracle.slice.opencode.OpenCodeRun` / :class:`OpenCodeAdapter` from a
package descriptor, so the G3a seam uses the same pinned binary, model, prompt
and tracked configuration template that P3 certified. Tests can inspect the
binding offline without spawning a model or requiring the OpenCode binary.
"""
from __future__ import annotations

from pathlib import Path

from oracle.function_agent.packages import FunctionPackage
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun

#: The model identity pinned by P3-B (see docs/oracle/P3_SLICE.md).
PINNED_PROVIDER = "opencode"
PINNED_MODEL_ID = "deepseek-v4.1-flash-free"
PINNED_VARIANT = "high"


def binding_descriptor() -> dict[str, str | None]:
    """The pin the seam inherits, for claims and tests."""
    return {
        "provider": PINNED_PROVIDER,
        "model": PINNED_MODEL_ID,
        "variant": PINNED_VARIANT,
        "template": "oracle/slice/opencode_config/opencode.json.template",
    }


def opencode_run(package: FunctionPackage, *, run_id: str, run_dir: Path,
                 workspace: Path, repo_root: Path, python: Path,
                 binary: Path, mode: str = "repair", **overrides) -> OpenCodeRun:
    """Build the pinned run description for ``package`` without starting it."""
    return OpenCodeRun(
        run_id=run_id,
        run_dir=Path(run_dir),
        workspace=Path(workspace),
        repo_root=Path(repo_root),
        python=Path(python),
        binary=Path(binary),
        provider=PINNED_PROVIDER,
        model_id=PINNED_MODEL_ID,
        variant=PINNED_VARIANT,
        prompt_path=package.prompt_path,
        template_dir=package.config_template_dir,
        mode=mode,
        **overrides,
    )


def opencode_adapter(package: FunctionPackage, **kwargs) -> OpenCodeAdapter:
    """Construct the certified OpenCode adapter for a package (no launch)."""
    return OpenCodeAdapter(opencode_run(package, **kwargs))
