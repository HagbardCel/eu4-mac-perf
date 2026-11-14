"""Cross-API semantic operation comparison and resource lineage (PR 4).

The two backends expose different APIs but the same engine contracts.  An
operation is normalized into the fields the reconstructed engine actually
depends on -- caller/draw identity, shader effect, topology, uniforms,
texture-unit bindings, render target, viewport/scissor, raster/depth/blend,
clear and resolve semantics -- without inventing a universal graphics IR.

``first_divergence`` reports the earliest confirmed semantic mismatch, or says
that the evidence is insufficient.  It never claims a root cause from a pixel
difference alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA = "gfx-semantic-diff/v1"
RESOURCE_SCHEMA = "gfx-resource-lineage/v1"
MATCH = "match"
DIVERGED = "diverged"
INSUFFICIENT = "insufficient"

FIELD_ORDER = (
    "kind",
    "draw_id",
    "caller_rva",
    "target",
    "target_size",
    "viewport",
    "scissor",
    "shader",
    "topology",
    "samplers",
    "textures",
    "uniforms",
    "raster",
    "depth",
    "blend",
    "clear",
    "resolve",
    "gpu",
)


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if type(value) is int:
        return value != 0
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in ("true", "yes", "on", "1", "enabled"):
            return True
        if lowered in ("false", "no", "off", "0", "disabled"):
            return False
    return None


def _texture_unit(name: str, declared: Any) -> int | None:
    if type(declared) is int and declared >= 0:
        return declared
    if name.startswith("tex") and name[3:].isdigit():
        return int(name[3:])
    return None


def normalize_samplers(samplers: Any) -> dict:
    """Normalize ``{sampler_name: {binding/unit, texture, filter...}}``.

    ``texN`` must bind to unit N.  The declared unit is retained so a mismatch
    is reported instead of being silently renumbered.
    """
    result: dict[str, dict] = {}
    if not isinstance(samplers, dict):
        return result
    for name, entry in samplers.items():
        if not isinstance(name, str):
            continue
        if isinstance(entry, (int, str)):
            entry = {"binding": entry}
        if not isinstance(entry, dict):
            continue
        declared = entry.get("unit", entry.get("binding"))
        unit = _texture_unit(name, declared)
        record = {
            "unit": unit,
            "texture": entry.get("texture") or entry.get("texture_id"),
            "digest": entry.get("digest") or entry.get("texture_digest"),
            "filter": entry.get("filter"),
            "wrap": entry.get("wrap"),
        }
        result[name] = record
    return result


def sampler_contract(samplers: dict) -> list[dict]:
    """Return explicit violations of the reconstructed ``texN -> unit N`` rule."""
    violations = []
    for name, record in samplers.items():
        if name.startswith("tex") and name[3:].isdigit():
            expected = int(name[3:])
            if record.get("unit") != expected:
                violations.append({
                    "sampler": name,
                    "expected_unit": expected,
                    "actual_unit": record.get("unit"),
                })
    return violations


def normalize_operation(operation: dict, backend: str) -> dict:
    """Normalize the comparable subset of one GL or Metal operation."""
    if not isinstance(operation, dict):
        raise ValueError("operation must be an object")
    kind = operation.get("kind")
    if not isinstance(kind, str):
        raise ValueError("operation.kind is required")
    result = {
        "kind": kind,
        "draw_id": operation.get("draw_id"),
        "caller_rva": operation.get("caller_rva"),
        "sequence": operation.get("sequence"),
        "target": operation.get("target") or operation.get("render_target"),
        "target_size": operation.get("target_size") or operation.get("extent"),
        "viewport": operation.get("viewport"),
        "scissor": operation.get("scissor"),
        "topology": operation.get("topology") or operation.get("primitive"),
        "shader": _normalize_shader(operation.get("shader")),
        "samplers": normalize_samplers(operation.get("samplers")),
        "textures": operation.get("textures") if isinstance(operation.get("textures"), dict) else {},
        "uniforms": operation.get("uniforms") if isinstance(operation.get("uniforms"), dict) else {},
        "raster": _normalize_raster(operation.get("raster")),
        "depth": _normalize_depth(operation.get("depth")),
        "blend": _normalize_blend(operation.get("blend")),
        "clear": operation.get("clear"),
        "resolve": _normalize_resolve(operation.get("resolve")),
        "gpu": operation.get("gpu") if isinstance(operation.get("gpu"), dict) else {},
        "backend": backend,
    }
    result["sampler_contract_violations"] = sampler_contract(result["samplers"])
    return result


def _normalize_shader(shader: Any) -> dict | None:
    if not isinstance(shader, dict):
        return None
    return {
        "identity": shader.get("identity") or shader.get("effect"),
        "vertex": shader.get("vertex") or shader.get("vs"),
        "pixel": shader.get("pixel") or shader.get("ps") or shader.get("fragment"),
        "variant": shader.get("variant"),
        "linked": _normalize_bool(shader.get("linked")),
    }


def _normalize_raster(raster: Any) -> dict | None:
    if not isinstance(raster, dict):
        return None
    return {
        "cull_enabled": _normalize_bool(raster.get("cull_enabled", raster.get("cull"))),
        "cull_face": raster.get("cull_face"),
        "front_face": raster.get("front_face") or raster.get("winding"),
        "fill_mode": raster.get("fill_mode"),
    }


def _normalize_depth(depth: Any) -> dict | None:
    if not isinstance(depth, dict):
        return None
    return {
        "test": _normalize_bool(depth.get("test", depth.get("depth_test"))),
        "write": _normalize_bool(depth.get("write", depth.get("depth_write"))),
        "func": depth.get("func") or depth.get("depth_func"),
        "has_attachment": _normalize_bool(depth.get("has_attachment")),
    }


def _normalize_blend(blend: Any) -> dict | None:
    if isinstance(blend, bool):
        return {"enabled": blend}
    if not isinstance(blend, dict):
        return None
    return {
        "enabled": _normalize_bool(blend.get("enabled", blend.get("blend_enable"))),
        "source": blend.get("source") or blend.get("src"),
        "destination": blend.get("destination") or blend.get("dst"),
        "alpha_source": blend.get("alpha_source"),
        "alpha_destination": blend.get("alpha_destination"),
    }


def _normalize_resolve(resolve: Any) -> dict | None:
    if not isinstance(resolve, dict):
        return None
    return {
        "source": resolve.get("source"),
        "destination": resolve.get("destination"),
        "source_size": resolve.get("source_size"),
        "destination_size": resolve.get("destination_size"),
        "flip_v": _normalize_bool(resolve.get("flip_v", resolve.get("flip"))),
        "scaled": _normalize_bool(resolve.get("scaled")),
        "status": resolve.get("status"),
    }


def _compare_field(name: str, reference: Any, candidate: Any) -> dict | None:
    if reference == candidate:
        return None
    if isinstance(reference, dict) and isinstance(candidate, dict):
        for key in sorted(set(reference) | set(candidate)):
            nested = _compare_field(f"{name}.{key}", reference.get(key),
                                    candidate.get(key))
            if nested is not None:
                return nested
        return None
    return {"field": name, "reference": reference, "candidate": candidate}


def compare_operations(reference: dict, candidate: dict) -> dict:
    """Compare two normalized operations and return the first mismatched field."""
    ref = normalize_operation(reference, reference.get("backend", "opengl"))
    cand = normalize_operation(candidate, candidate.get("backend", "metal"))
    checks = []
    for name in FIELD_ORDER:
        mismatch = _compare_field(name, ref.get(name), cand.get(name))
        if mismatch is not None:
            checks.append(mismatch)
            return {
                "status": DIVERGED,
                "first": mismatch,
                "checked": [item["field"] for item in checks],
                "reference": ref,
                "candidate": cand,
            }
        checks.append({"field": name, "status": "match"})
    violations = ref["sampler_contract_violations"] + cand["sampler_contract_violations"]
    if violations:
        result = {
            "status": DIVERGED,
            "first": {
                "field": "samplers.contract",
                "reference": ref["sampler_contract_violations"],
                "candidate": cand["sampler_contract_violations"],
            },
            "checked": [item["field"] for item in checks],
            "reference": ref,
            "candidate": cand,
        }
        return result
    return {
        "status": MATCH,
        "first": None,
        "checked": [item["field"] for item in checks],
        "reference": ref,
        "candidate": cand,
    }


def first_divergence(reference_operations: list[dict], candidate_operations: list[dict],
                     *, require_same_count: bool = True) -> dict:
    """Compare two operation sequences and localize the earliest mismatch."""
    ref = [op for op in reference_operations if isinstance(op, dict)]
    cand = [op for op in candidate_operations if isinstance(op, dict)]
    if not ref or not cand:
        return {
            "schema": SCHEMA,
            "status": INSUFFICIENT,
            "reason": "one side has no operations",
            "first": None,
        }
    pairs = min(len(ref), len(cand))
    for index in range(pairs):
        result = compare_operations(ref[index], cand[index])
        if result["status"] == DIVERGED:
            return {
                "schema": SCHEMA,
                "status": DIVERGED,
                "operation_index": index,
                "first": result["first"],
                "checks": result["checked"],
                "reference_operation": result["reference"],
                "candidate_operation": result["candidate"],
                "aligned_prefix": index,
                "original_operations": {
                    "reference": ref[index],
                    "candidate": cand[index],
                },
            }
    if len(ref) != len(cand):
        longer = "reference" if len(ref) > len(cand) else "candidate"
        return {
            "schema": SCHEMA,
            "status": INSUFFICIENT if not require_same_count else DIVERGED,
            "operation_index": pairs,
            "first": {
                "field": "operation_count",
                "reference": len(ref),
                "candidate": len(cand),
            },
            "reason": f"{longer} has additional operations beyond the common prefix",
            "aligned_prefix": pairs,
        }
    return {
        "schema": SCHEMA,
        "status": MATCH,
        "operation_index": None,
        "first": None,
        "aligned_prefix": pairs,
    }


# ---------------------------------------------------------------------------
# Resource lineage


@dataclass
class ResourceRecord:
    key: str
    kind: str = "texture"
    dimensions: tuple[int, int] | None = None
    format: str | None = None
    generation: int = 0
    created_at: int | None = None
    last_write_at: int | None = None
    last_write_digest: str | None = None
    last_writer: str | None = None
    consumers: list[str] = field(default_factory=list)
    bound_units: list[int] = field(default_factory=list)
    presented: bool = False
    destroyed_at: int | None = None
    uncertainty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "dimensions": list(self.dimensions) if self.dimensions else None,
            "format": self.format,
            "generation": self.generation,
            "created_at": self.created_at,
            "last_write_at": self.last_write_at,
            "last_write_digest": self.last_write_digest,
            "last_writer": self.last_writer,
            "consumers": list(self.consumers),
            "bound_units": list(self.bound_units),
            "presented": self.presented,
            "destroyed_at": self.destroyed_at,
            "uncertainty": list(self.uncertainty),
        }


class ResourceLineage:
    """Stable logical resource identities across two processes.

    Raw pointers are never used as cross-process identity.  A caller supplies a
    logical key (usually the engine texture name or a stable slot) and the
    operation sequence number; generation increments on each content upload.
    """

    def __init__(self):
        self.resources: dict[str, ResourceRecord] = {}
        self.operations: list[dict] = []

    def _record(self, key: str, kind: str = "texture") -> ResourceRecord:
        if key not in self.resources:
            self.resources[key] = ResourceRecord(key=key, kind=kind)
        return self.resources[key]

    def observe(self, event: dict) -> dict:
        if not isinstance(event, dict) or not isinstance(event.get("key"), str):
            raise ValueError("resource event needs a logical key")
        key = event["key"]
        action = event.get("action")
        sequence = event.get("sequence")
        record = self._record(key, str(event.get("kind", "texture")))
        if sequence is not None:
            self.operations.append({"key": key, "action": action,
                                    "sequence": sequence})
        if action == "create":
            record.created_at = sequence
            if isinstance(event.get("dimensions"), (list, tuple)) and len(event["dimensions"]) == 2:
                record.dimensions = (int(event["dimensions"][0]), int(event["dimensions"][1]))
            record.format = event.get("format")
        elif action in ("upload", "modify", "write"):
            if record.created_at is None:
                record.uncertainty.append("write_before_creation")
            record.generation += 1
            record.last_write_at = sequence
            record.last_write_digest = event.get("digest")
            record.last_writer = event.get("writer")
        elif action == "bind":
            unit = event.get("unit")
            if type(unit) is int:
                if unit not in record.bound_units:
                    record.bound_units.append(unit)
                if record.created_at is None:
                    record.uncertainty.append("bind_before_creation")
        elif action == "sample":
            generation = event.get("generation")
            consumer = event.get("consumer") or event.get("draw_id")
            if consumer and consumer not in record.consumers:
                record.consumers.append(consumer)
            if record.last_write_at is None:
                record.uncertainty.append("sampled_before_upload")
            elif type(generation) is int and generation != record.generation:
                record.uncertainty.append(
                    f"sampled_generation_{generation}_current_{record.generation}"
                )
        elif action in ("clear", "resolve"):
            record.generation += 1
            record.last_write_at = sequence
            record.last_writer = action
            record.last_write_digest = event.get("digest")
        elif action == "present":
            record.presented = True
        elif action == "destroy":
            record.destroyed_at = sequence
            if record.consumers:
                record.uncertainty.append("destroyed_after_sampling")
        return record.as_dict()

    def summary(self) -> dict:
        uncertain = {
            key: record.uncertainty for key, record in self.resources.items()
            if record.uncertainty
        }
        return {
            "schema": RESOURCE_SCHEMA,
            "resources": {key: record.as_dict() for key, record in self.resources.items()},
            "uncertain": uncertain,
            "operations": len(self.operations),
        }
