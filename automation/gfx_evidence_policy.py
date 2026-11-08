"""Minimum field-relevant provenance for provisional draw evidence.

This is not a semantic validator. It establishes that a field claiming
``established`` is backed by an original observation or independently verified
static source of the right kind, so an unrelated draw record cannot be handed to
contract qualification as if it were shader or texture evidence. The
independent contract reviewer still establishes what the evidence means.
"""

from __future__ import annotations

from typing import Any, Callable

SUBSTANTIVE = frozenset(
    {
        "draw_snapshot",
        "resource_observation",
        "draw_record",
        "primitive_set",
        "static_table",
        "static_reconstruction",
    }
)

Predicate = Callable[[list, dict], tuple[bool, str]]


def _items(evidence: list) -> list[dict]:
    return [item for item in evidence if isinstance(item, dict)]


def _selected_draw(items: list[dict], ctx: dict) -> bool:
    target = ctx.get("target")
    call_id = getattr(target, "call_id", None)
    if call_id is None:
        return False
    for item in items:
        if item.get("type") in ("draw_snapshot", "draw_record") \
                and item.get("call_id") == call_id:
            return True
    return False


def _shader_identity(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    if not _selected_draw(items, ctx):
        return False, "no selected-draw evidence"
    symbols = {
        "_GfxSetShader", "_GfxCreateShader", "_GfxAddVertexShader",
        "_GfxAddPixelShader", "_GfxLinkShader",
    }
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "captured shader object association"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "verified static shader association"
    return False, "no captured or reconstructed shader-object association"


def _shader_stage_sources(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    symbols = {"_GfxAddVertexShader", "_GfxAddPixelShader"}
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in symbols
        and item.get("payload")
        for item in items
    ):
        return True, "captured shader source bytes"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "verified static shader source"
    return False, "no captured or reconstructed shader source bytes"


def _texture_identity(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    symbols = {"_GfxSetTextures", "_GfxCreateTexture", "_GfxInitializeTexture"}
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in symbols
        and item.get("resource_id")
        for item in items
    ):
        return True, "texture binding or header observation"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "verified static texture identity"
    return False, "no texture binding or header observation"


def _texture_contents(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    symbols = {"_GfxInitializeTexture", "_GfxUpdateTexture", "_GfxUpdateTextureRectangle"}
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in symbols
        and item.get("payload")
        and item.get("resource_id")
        for item in items
    ):
        return True, "original texture upload payload"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "verified static texture upload"
    return False, "no original texture payload with resource identity"


def _sampler_state(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in {"_GfxSetTextures", "_GfxCreateSampler"}
        for item in items
    ):
        return True, "sampler observation"
    target = ctx.get("target")
    call_id = getattr(target, "call_id", None)
    if any(
        item.get("type") == "draw_snapshot"
        and item.get("call_id") == call_id
        and (item.get("sampler") is not None or item.get("field") == "sampler_state")
        for item in items
    ):
        return True, "draw-time sampler state"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in {"_GfxCreateSampler", "_GfxSetTextures", "default_sampler"}
        for item in items
    ):
        return True, "verified static sampler default"
    return False, "no sampler observation or verified default"


def _state_slice(evidence: list, ctx: dict, symbols: set[str]) -> tuple[bool, str]:
    items = _items(evidence)
    target = ctx.get("target")
    call_id = getattr(target, "call_id", None)
    if any(
        item.get("type") == "draw_snapshot"
        and item.get("call_id") == call_id
        and item.get("offset") is not None
        and item.get("length")
        for item in items
    ):
        return True, "draw-master state bytes"
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "state object observation"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in symbols
        for item in items
    ):
        return True, "verified static state decode"
    return False, "no relevant state bytes or state observation"


def _blend(evidence: list, ctx: dict) -> tuple[bool, str]:
    return _state_slice(evidence, ctx, {"_GfxSetBlendState", "_GfxCreateBlendState"})


def _depth(evidence: list, ctx: dict) -> tuple[bool, str]:
    return _state_slice(
        evidence, ctx, {"_GfxSetDepthStencilState", "_GfxCreateDepthStencilState"}
    )


def _rasterizer(evidence: list, ctx: dict) -> tuple[bool, str]:
    return _state_slice(
        evidence, ctx, {"_GfxSetRasterizerState", "_GfxCreateRasterizerState"}
    )


def _scissor(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") == "_GfxSetScissorRectangle"
        for item in items
    ):
        return True, "scissor observation"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in {"_GfxSetScissorRectangle", "default_scissor"}
        for item in items
    ):
        return True, "verified default scissor"
    return False, "no scissor observation or verified default"


def _attribute_layout(evidence: list, ctx: dict) -> tuple[bool, str]:
    items = _items(evidence)
    if any(
        item.get("type") == "resource_observation"
        and item.get("symbol") in {"_GfxSetShader", "_GfxCreateShader", "_GfxAddVertexLayout"}
        and item.get("payload")
        for item in items
    ):
        return True, "shader-object or attribute-table observation"
    if any(
        item.get("type") == "static_reconstruction"
        and item.get("symbol") in {"_GfxAddVertexLayout", "_GfxSetShader"}
        for item in items
    ):
        return True, "verified static attribute layout"
    return False, "no shader-object/attribute-table evidence"


FIELD_POLICY: dict[str, Predicate] = {
    "shader_identity": _shader_identity,
    "shader_stage_sources": _shader_stage_sources,
    "texture_slot_0_identity": _texture_identity,
    "texture_slot_0_contents": _texture_contents,
    "sampler_state": _sampler_state,
    "render_state_blend": _blend,
    "render_state_depth_stencil": _depth,
    "render_state_rasterizer": _rasterizer,
    "scissor_state": _scissor,
    "vertex_attribute_layout": _attribute_layout,
}


def evidence_relevant(
    field: str, evidence: list, ctx: dict
) -> tuple[bool, str]:
    """Return ``(relevant, basis_or_reason)`` for a field's evidence."""
    policy = FIELD_POLICY.get(field)
    if policy is not None:
        return policy(evidence, ctx)
    if any(
        isinstance(item, dict) and item.get("type") in SUBSTANTIVE
        for item in evidence
    ):
        return True, "generic substantive evidence"
    return False, "no substantive evidence"
