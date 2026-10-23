"""P4.10 (function half, A1): materialize a P4.9 observation export.

This is the missing bridge between the native recorder's *export* and the offline
record model. It consumes a parsed P4.9 transcript plus a reviewed **capture
declaration** and publishes, into the immutable evidence store:

* one schema-valid observation record per exported observation;
* one blob per complete resource version, assembled from its spans in declaration
  order and checked against a size the snapshot does not supply;
* a sealed **capture binding** that references the declaration, the export and every
  record, and whose verifier re-derives the whole binding from those sources.

Two identities stay separate on purpose. The native recorder decides whether an
observation was *captured*; this module decides whether that captured observation is
inside the declared semantic domain. A capture that is complete but whose external
effects were only aggregated is stored and accounted for, and gets no semantic
digest. Native counters are never rewritten to make finalization pass.

Resource identity is scoped by the owning world. For this producer a world is
``(capture, scenario, thread, slot)`` within a generation, declared by
``resource_scope``; two threads' or two scenarios' ``slot:0@0`` are different
resources even when their local identifiers match. That scoping is used for size
lookup, lifetime checks and for the resources a record projects; resource-bearing
behaviors from two scenarios therefore no longer deduplicate, which is correct
rather than convenient.

Verification has two modes. ``source-bound`` re-derives the binding from the export
and declaration and is the only mode admissible as proof of materialization;
``structural`` checks an already-sealed binding without its sources and is for
integrity inspection only.

It touches no process and never opens EU IV; every input is a file this repository's
own drivers wrote.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from oracle.recorder import finalize, harness, registry, schema
from oracle.recorder.store import EvidenceStore

DECLARATION_SCHEMA_VERSION = "eu4-oracle-capture-declaration/v1"
BINDING_SCHEMA_VERSION = "eu4-oracle-capture-binding/v1"

#: What the materialized evidence can be compared over. A1 is resource-observation
#: domain only; a function fixture is A2's artifact, not a reinterpretation of these.
DOMAIN = "resource-observation"

#: A1 can describe a producer, not attest a running module. The loaded-module kind is
#: introduced when its identity can be established independently (PR B).
MODULE_IDENTITY_KINDS = ("producer-source-bundle",)

#: How a resource lifetime is owned. A scenario owns independent worlds, one per
#: declared thread, so the lifetime key includes both.
RESOURCE_SCOPES = ("scenario-thread",)

EFFECT_COVERAGE = ("aggregate-only", "none")
CARRIER_BITS = (8, 16, 32, 64)

VERIFICATION_SOURCE_BOUND = "source-bound"
VERIFICATION_STRUCTURAL = "structural"

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_HOOK_KEY = re.compile(r"\A[0-9]+\Z")


class MaterializeError(ValueError):
    """A capture could not be materialized into honest, closed evidence."""


# --- the capture declaration ----------------------------------------------------

def declaration_digest(declaration: dict[str, Any]) -> str:
    """Integrity digest of the reviewed declaration the export is bound to."""
    return sha256(schema.canonical_bytes(declaration)).hexdigest()


def policy_digest(policy: dict[str, Any]) -> str:
    """Identity of one effective capture policy, as declared per scenario."""
    projection = {"name": policy["name"], "deep_hooks": sorted(policy["deep_hooks"])}
    return sha256(schema.canonical_bytes(projection)).hexdigest()


def load_declaration(path: str | Path) -> dict[str, Any]:
    """Read and validate a capture declaration from JSON."""
    try:
        declaration = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializeError(f"cannot read capture declaration {path}: {exc}") from exc
    validate_declaration(declaration)
    return declaration


def validate_declaration(declaration: Any) -> None:
    """Reject a declaration that could not honestly fill a record's provenance/ABI."""
    if not isinstance(declaration, dict):
        raise MaterializeError("capture declaration is not an object")
    if declaration.get("schema_version") != DECLARATION_SCHEMA_VERSION:
        raise MaterializeError(
            f"unsupported capture declaration schema: {declaration.get('schema_version')!r}")
    for key in ("capture_id", "epoch", "calling_convention"):
        if not isinstance(declaration.get(key), str) or not declaration[key]:
            raise MaterializeError(f"capture declaration is missing {key!r}")
    module = declaration.get("module_sha256")
    if not isinstance(module, str) or not _SHA256.match(module):
        raise MaterializeError("module_sha256 must be a lowercase sha256")
    if declaration.get("module_identity") not in MODULE_IDENTITY_KINDS:
        raise MaterializeError(
            "A1 can only declare a producer-source-bundle module identity; a "
            "loaded-module identity requires independent verification")
    if declaration.get("resource_scope") not in RESOURCE_SCOPES:
        raise MaterializeError(f"resource_scope must be one of {RESOURCE_SCOPES}")
    if declaration.get("effects") not in EFFECT_COVERAGE:
        raise MaterializeError(f"effects must be one of {EFFECT_COVERAGE}")

    fidelity = declaration.get("fidelity")
    if not isinstance(fidelity, dict) or fidelity.get("mode") not in schema.FIDELITY_MODES:
        raise MaterializeError(f"fidelity.mode must be one of {schema.FIDELITY_MODES}")
    if not isinstance(fidelity.get("policy_version"), str) or not fidelity["policy_version"]:
        raise MaterializeError("fidelity.policy_version must be a non-empty string")

    size = declaration.get("resource_size")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise MaterializeError("resource_size must be a non-negative integer or null")

    hooks = declaration.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise MaterializeError("capture declaration declares no hooks")
    for hook_id, hook in hooks.items():
        if not _HOOK_KEY.match(str(hook_id)):
            raise MaterializeError(f"hook id is not numeric: {hook_id!r}")
        if not isinstance(hook, dict) or not isinstance(hook.get("name"), str):
            raise MaterializeError(f"hook {hook_id} has no name")
        arity = hook.get("arity")
        if not isinstance(arity, int) or isinstance(arity, bool) or arity < 0:
            raise MaterializeError(f"hook {hook_id} must declare a non-negative arity")
        carriers = hook.get("carriers")
        if not isinstance(carriers, list):
            raise MaterializeError(f"hook {hook_id} carriers must be a list")
        names: set[str] = set()
        indices: set[int] = set()
        for carrier in carriers:
            if not isinstance(carrier, dict):
                raise MaterializeError(f"hook {hook_id} carrier is not an object")
            name = carrier.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise MaterializeError(f"hook {hook_id} carrier names must be unique")
            names.add(name)
            if carrier.get("kind") not in schema.CARRIER_KINDS:
                raise MaterializeError(
                    f"hook {hook_id} carrier {name!r} has an unknown kind")
            if carrier.get("bits") not in CARRIER_BITS:
                raise MaterializeError(
                    f"hook {hook_id} carrier {name!r} bits must be one of {CARRIER_BITS}")
            arg = carrier.get("arg")
            if not isinstance(arg, int) or isinstance(arg, bool) or arg < 0:
                raise MaterializeError(
                    f"hook {hook_id} carrier {name!r} must name an argument index")
            if arg >= arity:
                raise MaterializeError(
                    f"hook {hook_id} carrier {name!r} names an argument beyond its arity")
            indices.add(arg)
        if indices != set(range(arity)):
            # A shape with an unnamed argument cannot distinguish "unused" from
            # "forgotten"; the reviewed declaration must describe every argument.
            raise MaterializeError(
                f"hook {hook_id} carriers must cover every argument 0..{arity - 1}")

    scenarios = declaration.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise MaterializeError("capture declaration covers no scenarios")
    for name, cfg in scenarios.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("policy"), dict):
            raise MaterializeError(f"scenario {name!r} declares no effective policy")
        policy = cfg["policy"]
        if not isinstance(policy.get("name"), str) or not policy["name"]:
            raise MaterializeError(f"scenario {name!r} policy has no name")
        deep = policy.get("deep_hooks")
        if not isinstance(deep, list) or not all(
                isinstance(h, int) and not isinstance(h, bool) for h in deep):
            raise MaterializeError(f"scenario {name!r} deep_hooks must be integers")
        unknown = [h for h in deep if str(h) not in hooks]
        if unknown:
            raise MaterializeError(
                f"scenario {name!r} selects undeclared hooks {sorted(unknown)}")


# --- derivation (pure: reads no store, writes nothing) --------------------------

def _plan_export(export_text: str, declaration: dict[str, Any]) -> dict[str, Any]:
    """Derive every record, blob and accounting result without publishing anything."""
    parsed = harness.parse_export_transcript(export_text)
    scenarios: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    accounted: dict[str, int] = {}
    for scenario in parsed["scenarios"]:
        entry, accepted, content = _plan_scenario(scenario, declaration)
        scenarios.append(entry)
        if entry["outcome"] != "materialized":
            continue
        payloads.extend(accepted)
        blobs.update(content)
        for key, value in entry["counters"].items():
            accounted[key] = accounted.get(key, 0) + value
    if not accounted:
        accounted = {name: 0 for name in finalize.REQUIRED_COUNTERS}
    ledger = finalize.classify([schema.seal(payload) for payload in payloads], accounted)
    return {"scenarios": scenarios, "payloads": payloads, "blobs": blobs,
            "accounted": accounted, "ledger": ledger}


def _plan_scenario(scenario: dict[str, Any], declaration: dict[str, Any]
                   ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bytes]]:
    entry: dict[str, Any] = {
        "name": scenario["name"],
        "accounting": scenario["accounting"],
        "admission": scenario["admission"],
        "listed": scenario["listed"],
        "counters": {key: int(value) for key, value in scenario["counters"].items()},
        "policy": None,
        "outcome": "rejected",
        "reason": None,
        "records": [],
    }

    cfg = declaration["scenarios"].get(scenario["name"])
    if cfg is None:
        return _reject(entry, "no reviewed producer configuration for this scenario"), [], {}
    policy = {"name": cfg["policy"]["name"], "deep_hooks": sorted(cfg["policy"]["deep_hooks"])}
    entry["policy"] = {"name": policy["name"], "sha256": policy_digest(policy),
                       "deep_hooks": policy["deep_hooks"],
                       # The digest names the declared policy; the export cannot attest
                       # that the producer actually applied it.
                       "attested": False}

    if scenario["admission"] != "ok":
        return _reject(entry, "admission rejected by the capture"), [], {}
    if not scenario["listed"]:
        entry["outcome"] = "withheld"
        entry["reason"] = "the producer deliberately printed no observations"
        return entry, [], {}

    observations = scenario["observations"]
    for observation in observations:
        hook_id = str(observation["hook_id"])
        if hook_id not in declaration["hooks"]:
            return _reject(entry, f"hook {hook_id} is not declared"), [], {}
        if observation["hook_id"] not in policy["deep_hooks"]:
            return _reject(
                entry, f"hook {hook_id} was printed but the declared policy excludes it"), [], {}
    try:
        registry.verify_closure(observations)
    except registry.RegistryError as exc:
        return _reject(entry, f"closure failed: {exc}"), [], {}

    capture_id = declaration["capture_id"]
    sizes = _creating_sizes(observations, declaration, capture_id, scenario["name"])
    default_size = declaration.get("resource_size")
    payloads: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    try:
        for observation in observations:
            payload, content = _payload(observation, scenario["name"], declaration,
                                        entry["policy"], capture_id, sizes, default_size)
            payloads.append(payload)
            if content is not None:
                blobs[sha256(content).hexdigest()] = content
    except (MaterializeError, registry.RegistryError) as exc:
        # Nothing from a rejected scenario is published, including its content.
        return _reject(entry, str(exc)), [], {}

    for payload in payloads:
        gaps = schema.qualification_gaps(payload)
        native = payload["completeness"]["state"]
        semantic = ("incomplete" if native == "incomplete"
                    else "domain-incomplete" if gaps else "qualified")
        entry["records"].append({
            "digest": schema.record_digest(payload),
            "native_state": native,
            "semantic_state": semantic,
            "gaps": list(gaps),
            "resources": _bound_resources(payload),
        })
    entry["outcome"] = "materialized"
    return entry, payloads, blobs


def _reject(entry: dict[str, Any], reason: str) -> dict[str, Any]:
    entry["outcome"] = "rejected"
    entry["reason"] = reason
    entry["records"] = []
    return entry


def _creating_sizes(observations: Iterable[dict[str, Any]], declaration: dict[str, Any],
                    capture_id: str, scenario: str) -> dict[tuple[Any, ...], int]:
    """Independent version sizes: a creating call's declared size, per lifetime.

    The size deliberately does not come from the snapshot it is checking. A producer
    that creates the resource in this capture states it; otherwise the reviewed
    declaration's producer constant is the only independent source, and an absent one
    makes the scenario unattributable.
    """
    sizes: dict[tuple[Any, ...], int] = {}
    for observation in observations:
        hook = declaration["hooks"][str(observation["hook_id"])]
        if hook["name"] != "create" or observation["complete"] != 1:
            continue
        if observation["fault"] != "none" or not observation["args"]:
            continue
        try:
            origin = registry.reference_from_context(observation["origin"])
        except registry.RegistryError:
            continue
        sizes[(capture_id, scenario, int(observation["thread_id"]),
               origin.slot, origin.generation)] = int(observation["args"][0])
    return sizes


def _payload(observation: dict[str, Any], scenario: str, declaration: dict[str, Any],
             policy: dict[str, Any], capture_id: str, sizes: dict[tuple[Any, ...], int],
             default_size: int | None) -> tuple[dict[str, Any], bytes | None]:
    hook = declaration["hooks"][str(observation["hook_id"])]
    carriers, missing, unexpected, abi_confidence = _carriers(hook, observation)
    version, span_meta, content = _version(
        observation, scenario, capture_id, sizes, default_size)
    resources = []
    if version is not None:
        resources.append({
            "resource_id": version["resource_id"],
            "generation": version["generation"],
            "version": version["version"],
            "contents_sha256": version["contents_sha256"],
            "aliases": [],
        })
    native = "captured" if observation["complete"] == 1 else "incomplete"
    thread = int(observation["thread_id"])
    return {
        "record_type": "observation",
        "provenance": {
            "module_sha256": declaration["module_sha256"],
            "function_id": hook["name"],
            "callsite": "unavailable",
            "capture_policy_digest": policy["sha256"],
            "scenario": scenario,
            "epoch": declaration["epoch"],
            "thread": thread,
        },
        "invocation": {
            "hook_id": f"hook:{observation['hook_id']}",
            "call_id": f"{capture_id}:{scenario}:{thread}:{observation['sequence']}",
            "parent_span": None,
            "sequence": int(observation["sequence"]),
        },
        "abi": {
            "calling_convention": declaration["calling_convention"],
            "carriers": carriers,
            "return_carrier": None,
            "confidence": abi_confidence,
        },
        "state": {
            # A P4.9 resource observation declares no memory state; inventing an
            # empty one as evidence would be the defect. Its snapshot is the resource
            # version, and A2 -- not this record -- owns function state.
            "pre": [],
            "post": [],
            # Missing values are only informative for a complete observation; an
            # extra argument is an unknown input no matter how complete it is.
            "unknown_dependencies": sorted(
                (missing if native == "captured" else []) + unexpected),
        },
        "resources": resources,
        "effects": {
            "ordered": [],
            "complete": declaration["effects"] == "none",
        },
        "completeness": {
            "state": native,
            "losses": 0,
            "unavailable_dependencies": _dependencies(observation),
        },
        "fidelity": {
            "mode": declaration["fidelity"]["mode"],
            "policy_version": declaration["fidelity"]["policy_version"],
        },
        "raw": {
            "domain": DOMAIN,
            "hook_id": observation["hook_id"],
            "phase": observation["phase"],
            "point": observation["point"],
            "result": observation["result"],
            "fault": observation["fault"],
            "args": list(observation["args"]),
            "context": list(observation["context"]),
            "origin": list(observation["origin"]),
            "content": span_meta,
        },
    }, content


def _carriers(hook: dict[str, Any], observation: dict[str, Any]
              ) -> tuple[list[dict[str, Any]], list[str], list[str], str]:
    """Declared ABI carriers, with absent and extra values handled explicitly.

    A missing declared value is unknown rather than invented zero. An observed value
    beyond the declared arity is never silently dropped: it becomes an unknown input
    that disqualifies the record from semantic comparison, and its bytes stay in the
    record's raw evidence.
    """
    faulted = observation["fault"] != "none"
    args = [] if faulted else list(observation["args"])
    carriers: list[dict[str, Any]] = []
    missing: list[str] = []
    for spec in hook["carriers"]:
        width = spec["bits"] // 8
        if spec["arg"] < len(args):
            value = int(args[spec["arg"]]).to_bytes(width, "little").hex()
            confidence = "observed"
        else:
            value = "00" * width
            confidence = "unknown"
            missing.append(f"args:{hook['name']}:{spec['name']}")
        carriers.append({"name": spec["name"], "kind": spec["kind"], "bits": spec["bits"],
                         "value_hex": value, "confidence": confidence})
    unexpected = ([] if faulted else
                  [f"args:unexpected:{hook['name']}:{index}"
                   for index in range(hook["arity"], len(args))])
    return carriers, missing, unexpected, (
        "observed" if not missing and not unexpected else "unknown")


def _version(observation: dict[str, Any], scenario: str, capture_id: str,
             sizes: dict[tuple[Any, ...], int], default_size: int | None
             ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bytes | None]:
    """Assemble one whole resource version, or explain why there is none.

    A complete observation with declared content must produce exactly one whole
    version; an incomplete one produces none and keeps its spans diagnostic.
    """
    spans = observation["content"]
    meta = [{"status": span["status"], "length": span["length"],
             "sha256": (sha256(span["bytes"]).hexdigest()
                        if span["status"] == "captured" else None)}
            for span in spans]
    if not observation["complete"]:
        return None, meta, None
    if any(span["status"] != "captured" for span in spans):
        raise MaterializeError(
            f"observation {observation['sequence']} is complete but declares a lost span")
    if not spans:
        return None, meta, None
    try:
        origin = registry.reference_from_context(observation["origin"])
    except registry.RegistryError as exc:
        raise MaterializeError(f"content has no usable origin: {exc}") from exc
    thread = int(observation["thread_id"])
    key = (capture_id, scenario, thread, origin.slot, origin.generation)
    size = sizes.get(key, default_size)
    if size is None:
        raise MaterializeError(
            f"no independent size for {origin.named} in {scenario}/{thread} "
            f"(observation {observation['sequence']})")
    data = b"".join(span["bytes"] for span in spans)
    if len(data) != size:
        raise MaterializeError(
            f"resource version {scenario}/{thread} {origin.named} v{origin.version} is "
            f"{len(data)} bytes but its declared size is {size}")
    return {
        "resource_id": f"world:{capture_id}/{scenario}/{thread}:slot:{origin.slot}",
        "generation": origin.generation,
        "version": origin.version,
        "contents_sha256": sha256(data).hexdigest(),
    }, meta, data


def _dependencies(observation: dict[str, Any]) -> list[str]:
    """Unresolved lifetimes this observation names, if any.

    Complete observations have none (closure refused them earlier); incomplete ones
    keep the dependency they could not resolve, so it stays visible rather than being
    read as a live resource.
    """
    named: set[str] = set()
    if len(observation["context"]) == registry.CONTEXT_FIELDS:
        reference = registry.reference_from_context(observation["context"])
        if reference.valid and not reference.resolved:
            named.add(reference.named)
    if observation["content"] and len(observation["origin"]) == registry.CONTEXT_FIELDS:
        origin = registry.reference_from_context(observation["origin"])
        if origin.valid and not origin.resolved:
            named.add(origin.named)
    return sorted(named)


def _bound_resources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The binding's view of a record's whole versions (sizes and span slices)."""
    bound: list[dict[str, Any]] = []
    raw = payload["raw"]["content"]
    for resource in payload["resources"]:
        bound.append({
            "resource_id": resource["resource_id"],
            "generation": resource["generation"],
            "version": resource["version"],
            "contents_sha256": resource["contents_sha256"],
            "size": sum(span["length"] for span in raw),
            "spans": [dict(span) for span in raw],
        })
    return bound


# --- the binding and publication -----------------------------------------------

def binding_digest(binding: dict[str, Any]) -> str:
    """Integrity digest of a capture binding (also its blob name in the store)."""
    return sha256(schema.canonical_bytes(binding)).hexdigest()


def _build_binding(plan: dict[str, Any], declaration: dict[str, Any],
                   export_text: str) -> dict[str, Any]:
    """Assemble the deterministic binding for a derivation."""
    binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "capture_id": declaration["capture_id"],
        "epoch": declaration["epoch"],
        "domain": DOMAIN,
        "resource_scope": declaration["resource_scope"],
        "unavailable_fields": ["callsite"],
        "module": {
            "sha256": declaration["module_sha256"],
            "identity_kind": declaration["module_identity"],
            # A1 can name the producer configuration; it cannot attest the module
            # that actually ran. The loaded-module path is refused by validation.
            "attested": False,
        },
        "declaration": {
            "sha256": declaration_digest(declaration),
            "capture_id": declaration["capture_id"],
            "epoch": declaration["epoch"],
            "fidelity": declaration["fidelity"],
            "effects": declaration["effects"],
        },
        "export_sha256": sha256(export_text.encode("utf-8")).hexdigest(),
        "accounted_counters": dict(sorted(plan["accounted"].items())),
        "scenarios": plan["scenarios"],
        "ledger": plan["ledger"],
    }
    _check_version_uniqueness(binding["scenarios"])
    return binding


def _check_version_uniqueness(scenarios: Iterable[dict[str, Any]]) -> None:
    """One scoped resource version cannot carry two different contents."""
    seen: dict[tuple[str, int, int], str] = {}
    for scenario in scenarios:
        for meta in scenario["records"]:
            for resource in meta["resources"]:
                key = (resource["resource_id"], resource["generation"],
                       resource["version"])
                previous = seen.get(key)
                if previous is not None and previous != resource["contents_sha256"]:
                    raise MaterializeError(
                        f"resource version {key[0]}@{key[1]} v{key[2]} is bound to two "
                        "different contents")
                seen[key] = resource["contents_sha256"]


def materialize_export(export_text: str, declaration: dict[str, Any],
                       store: EvidenceStore) -> dict[str, Any]:
    """Materialize a P4.9 export into records, whole-version blobs and a binding.

    Returns the sealed binding. Every scenario is accounted for: it is either
    ``materialized``, ``withheld`` (the producer deliberately printed no
    observations) or ``rejected`` (admission, closure, policy or attribution failed),
    and a rejected or withheld scenario contributes no records or blobs.
    """
    validate_declaration(declaration)
    plan = _plan_export(export_text, declaration)
    # Every pure failure (including a version-content conflict) happens before the
    # first write. shortcut: publication is ordered blobs -> records -> binding, so
    # accepted evidence is complete before it is referenced; a crash mid-publication
    # can leave unreferenced artifacts, and a retry is deterministic.
    binding = _build_binding(plan, declaration, export_text)
    for _, content in sorted(plan["blobs"].items()):
        store.put_blob(content)
    for payload in plan["payloads"]:
        store.add_record(payload)
    store.put_blob(schema.canonical_bytes(binding))
    store.write_manifest()
    verification = verify_binding(store, binding, export_text=export_text,
                                  declaration=declaration)
    if verification["mode"] != VERIFICATION_SOURCE_BOUND:
        raise MaterializeError("materialization did not verify against its source")
    return binding


# --- verification ---------------------------------------------------------------

def verify_binding(store: EvidenceStore, binding: dict[str, Any], *,
                   export_text: str | None = None,
                   declaration: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify a sealed capture binding and report the mode of the proof.

    With **both** the export and the declaration, the binding is re-derived from
    scratch and must match exactly; every derived record and blob must then be
    present and byte-identical in the store. That is ``source-bound`` and is the only
    mode admissible as proof of materialization (``admitted: true``).

    Without the sources, the binding's own structure, provenance, resource
    associations and ledger are checked. That is ``structural`` inspection: it proves
    integrity, not that the records derive from the export, and it is never admitted.

    Verification is read-only: it publishes and updates nothing.
    """
    if (export_text is None) != (declaration is None):
        raise MaterializeError(
            "source-bound verification needs both the export and the declaration")
    if not isinstance(binding, dict) or binding.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise MaterializeError("not a capture binding")
    data = schema.canonical_bytes(binding)
    digest = sha256(data).hexdigest()
    if digest not in store.manifest()["payload"]["blobs"]:
        raise MaterializeError("capture binding is not sealed in the store manifest")
    if store.get_blob(digest) != data:
        raise MaterializeError("sealed capture binding does not match its store blob")

    if export_text is not None and declaration is not None:
        plan = _plan_export(export_text, declaration)
        expected = _build_binding(plan, declaration, export_text)
        if binding != expected:
            raise MaterializeError("capture binding does not re-derive from the source export")
        for payload in plan["payloads"]:
            record = schema.record_digest(payload)
            if store.get_record(record) != payload:
                raise MaterializeError(f"bound record {record} does not match the export")
        for blob, content in plan["blobs"].items():
            if store.get_blob(blob) != content:
                raise MaterializeError(f"bound content {blob} does not match the export")
        return {"binding_sha256": digest, "mode": VERIFICATION_SOURCE_BOUND,
                "admitted": True}

    _verify_structure(store, binding)
    return {"binding_sha256": digest, "mode": VERIFICATION_STRUCTURAL,
            "admitted": False}


def _verify_structure(store: EvidenceStore, binding: dict[str, Any]) -> None:
    """Re-establish the binding's internal consistency without its sources."""
    _check_version_uniqueness(binding["scenarios"])
    seen: set[str] = set()
    sealed_records: list[dict[str, Any]] = []
    accounted: dict[str, int] = {}
    for scenario in binding["scenarios"]:
        outcome = scenario.get("outcome")
        if outcome not in ("materialized", "withheld", "rejected"):
            raise MaterializeError(f"unknown scenario outcome {outcome!r}")
        counters = scenario.get("counters") or {}
        if outcome != "materialized":
            if scenario.get("records"):
                raise MaterializeError("a non-materialized scenario declares records")
            continue
        records = scenario.get("records") or []
        expected = int(counters.get("captured", 0)) + int(counters.get("incomplete", 0))
        if len(records) != expected:
            raise MaterializeError(
                f"scenario {scenario['name']!r} declares {expected} materializable "
                f"observations but binds {len(records)}")
        for meta in records:
            record_id = meta["digest"]
            if record_id in seen:
                raise MaterializeError(f"record {record_id} is bound twice")
            seen.add(record_id)
            if record_id not in store.records:
                raise MaterializeError(f"record {record_id} is not in the store manifest")
            payload = store.get_record(record_id)
            if payload["provenance"]["scenario"] != scenario["name"]:
                raise MaterializeError("record provenance names another scenario")
            if payload["provenance"]["capture_policy_digest"] != scenario["policy"]["sha256"]:
                raise MaterializeError("record policy does not match the effective policy")
            if payload["provenance"]["module_sha256"] != binding["module"]["sha256"]:
                raise MaterializeError("record module does not match the binding")
            gaps = schema.qualification_gaps(payload)
            native = payload["completeness"]["state"]
            semantic = ("incomplete" if native == "incomplete"
                        else "domain-incomplete" if gaps else "qualified")
            if meta["semantic_state"] != semantic or list(meta["gaps"]) != list(gaps):
                raise MaterializeError("record qualification does not re-derive")
            _verify_resources(store, payload, meta)
            sealed_records.append(schema.seal(payload))
        for key, value in counters.items():
            accounted[key] = accounted.get(key, 0) + int(value)

    if not accounted:
        accounted = {name: 0 for name in finalize.REQUIRED_COUNTERS}
    if accounted != binding["accounted_counters"]:
        raise MaterializeError("accounted counters do not re-derive")
    if finalize.classify(sealed_records, accounted) != binding["ledger"]:
        raise MaterializeError("ledger does not re-derive from the bound records")


def _verify_resources(store: EvidenceStore, payload: dict[str, Any],
                      meta: dict[str, Any]) -> None:
    """Bind one record's resource versions to stored whole-version blobs."""
    declared = payload["resources"]
    bound = meta.get("resources") or []
    if len(declared) != len(bound):
        raise MaterializeError("record resources do not match the binding")
    for resource, entry in zip(declared, bound):
        for key in ("resource_id", "generation", "version", "contents_sha256"):
            if resource[key] != entry[key]:
                raise MaterializeError(f"resource {key} does not match the record")
        blob = store.get_blob(entry["contents_sha256"])
        if len(blob) != entry["size"]:
            raise MaterializeError("whole-version blob has an unexpected length")
        spans = entry.get("spans") or []
        if sum(span["length"] for span in spans) != entry["size"]:
            raise MaterializeError("span lengths do not reconstruct the version length")
        offset = 0
        for span in spans:
            if span.get("sha256") is None:
                raise MaterializeError("a complete version has a lost span")
            chunk = blob[offset:offset + span["length"]]
            if sha256(chunk).hexdigest() != span["sha256"]:
                raise MaterializeError("span does not match its place in the version")
            offset += span["length"]
