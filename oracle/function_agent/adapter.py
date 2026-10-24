"""The G3a function-agent adapter: a thin seam over a certified P3 adapter.

The adapter performs no lifecycle work of its own. It delegates one attempt to an
inner adapter that already speaks the P3 ``AgentAdapter`` protocol (the
deterministic stub, the generic command adapter, or the pinned OpenCode adapter),
and it narrows the tool client to the package's five-tool vocabulary. Budget,
attempts, the wall clock, process supervision, freezing, certification and
receipts remain the controller's, exactly as in P3.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from oracle.function_agent import catalog
from oracle.function_agent.packages import FunctionPackage
from oracle.slice.adapters import AttemptContext, AttemptResult


class FunctionAgentAdapter:
    """Wraps a P3 agent adapter for one :class:`FunctionPackage`.

    This is intentionally not a controller and not an agent SDK. The only glue it
    adds is (a) package/catalog validation and (b) a catalog-bound tool client, so
    an inner agent cannot reach a tool outside the certified five.
    """

    def __init__(self, package: FunctionPackage, inner: Any):
        if package.arbitrary_cpp_supported or package.generic_workspace:
            raise ValueError(
                "the G3a seam does not support arbitrary reconstructed C++ functions")
        catalog.validate_catalog()
        if tuple(package.tool_names) != catalog.AGENT_VISIBLE_TOOLS:
            raise catalog.CatalogError(
                f"package tool vocabulary {list(package.tool_names)} is not the certified "
                f"five-tool surface {list(catalog.AGENT_VISIBLE_TOOLS)}")
        self.package = package
        self.inner = inner
        self.name = f"function-agent:{getattr(inner, 'name', type(inner).__name__)}"
        self._inner = inner

    def tool_catalog(self) -> dict[str, Any]:
        return catalog.catalog_report()

    def step(self, ctx: AttemptContext) -> AttemptResult:
        bound = catalog.CatalogBoundTools(ctx.tools, allowed=self.package.tool_names)
        return self._inner.step(replace(ctx, tools=bound))

    def receipt_metadata(self) -> dict[str, Any] | None:
        """Delegate the inner binding verbatim; the controller validates it.

        A stub or command adapter has no external toolchain identity and returns
        ``None``. The pinned OpenCode adapter returns the binary/model/config/
        prompt/event identities that the controller folds into the sealed receipt.
        """
        getter = getattr(self._inner, "receipt_metadata", None)
        if getter is None:
            return None
        return getter()
