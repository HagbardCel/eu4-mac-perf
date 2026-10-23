"""P4.10 (function half, A1): materialize a P4.9 observation export.

This is the missing bridge between the native recorder's *export* and the offline
record model. It consumes a parsed P4.9 transcript plus a reviewed **capture
declaration** and publishes, into the immutable evidence store:

* one schema-valid observation record per exported observation;
* one blob per complete resource version, assembled from its spans in declaration
  order and checked against a size the snapshot does not supply;
* a sealed **capture binding** that references the declaration, the export and every
  record, and whose verifier establishes the blob-to-record association the store's
  own integrity check cannot.

Two identities stay separate on purpose. The native recorder decides whether an
observation was *captured*; this module decides whether that captured observation is
inside the declared semantic domain. A capture that is complete but whose external
effects were only aggregated is stored and accounted for, and gets no semantic
digest. Native counters are never rewritten to make finalization pass.

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

MODULE_IDENTITY_KINDS = ("producer-source-bundle", "loaded-module")
EFFECT_COVERAGE = ("aggregate-only", "none")
CARRIER_BITS = (8, 16, 32, 64)

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
            f"module_identity must be one of {MODULE_IDENTITY_KINDS}")
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
        carriers = hook.get("carriers")
        if not isinstance(carriers, list):
            raise MaterializeError(f"hook {hook_id} carriers must be a list")
        names: set[str] = set()
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


# --- materialization ------------------------------------------------------------

def materialize_export(export_text: str, declaration: dict[str, Any],
                       store: EvidenceStore) -> dict[str, Any]:
    """Materialize a P4.9 export into records, whole-version blobs and a binding.

    Returns the sealed binding. Every scenario is accounted for: it is either
    ``materialized``, ``withheld`` (the producer deliberately printed no
    observations) or ``rejected`` (admission, closure, policy or attribution failed),
    and a rejected or withheld scenario contributes no records.
    """
    validate_declaration(declaration)
    parsed = harness.parse_export_transcript(export_text)

    entries: list[dict[str, Any]] = []
    sealed_records: list[dict[str, Any]] = []
    accounted: dict[str, int] = {}
    for scenario in parsed["scenarios"]:
        entry = _materialize_scenario(scenario, declaration, store)
        entries.append(entry)
        if entry["outcome"] != "materialized":
            continue
        for meta in entry["records"]:
            sealed_records.append(schema.seal(store.get_record(meta["digest"])))
        for key, value in entry["counters"].items():
            accounted[key] = accounted.get(key, 0) + value

    if not accounted:
        accounted = {name: 0 for name in finalize.REQUIRED_COUNTERS}
    ledger = finalize.classify(sealed_records, accounted)
    binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "capture_id": declaration["capture_id"],
        "epoch": declaration["epoch"],
        "domain": DOMAIN,
        "unavailable_fields": ["callsite"],
        "module": {
            "sha256": declaration["module_sha256"],
            "identity_kind": declaration["module_identity"],
            "attested": declaration["module_identity"] == "loaded-module",
        },
        "declaration": {
            "sha256": declaration_digest(declaration),
            "capture_id": declaration["capture_id"],
            "epoch": declaration["epoch"],
            "fidelity": declaration["fidelity"],
            "effects": declaration["effects"],
        },
        "export_sha256": sha256(export_text.encode("utf-8")).hexdigest(),
        "accounted_counters": dict(sorted(accounted.items())),
        "scenarios": entries,
        "ledger": ledger,
    }
    store.put_blob(schema.canonical_bytes(binding))
    store.write_manifest()
    verify_binding(store, binding, export_text=export_text, declaration=declaration)
    return binding


def binding_digest(binding: dict[str, Any]) -> str:
    """Integrity digest of a capture binding (also its blob name in the store)."""
    return sha256(schema.canonical_bytes(binding)).hexdigest()


def verify_binding(store: EvidenceStore, binding: dict[str, Any], *,
                   export_text: str | None = None,
                   declaration: dict[str, Any] | None = None) -> str:
    """Re-establish the properties ``EvidenceStore.verify`` does not.

    The binding must be sealed in the store; every referenced record must exist and
    re-validate; each record's provenance and resource versions must match the
    binding; every whole-version blob must exist, hash to the record's
    ``contents_sha256``, and still concatenate from its recorded spans; and the ledger
    must re-derive from the stored records and accounted counters.
    """
    if not isinstance(binding, dict) or binding.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise MaterializeError("not a capture binding")
    data = schema.canonical_bytes(binding)
    digest = sha256(data).hexdigest()
    if store.get_blob(digest) != data:
        raise MaterializeError("sealed capture binding does not match its store blob")

    if declaration is not None:
        if binding["declaration"]["sha256"] != declaration_digest(declaration):
            raise MaterializeError("binding does not reference this declaration")
    if export_text is not None:
        if binding["export_sha256"] != sha256(export_text.encode("utf-8")).hexdigest():
            raise MaterializeError("binding does not reference this export")

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
    return digest


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


# --- one scenario ---------------------------------------------------------------

def _materialize_scenario(scenario: dict[str, Any], declaration: dict[str, Any],
                          store: EvidenceStore) -> dict[str, Any]:
    name = scenario["name"]
    entry: dict[str, Any] = {
        "name": name,
        "accounting": scenario["accounting"],
        "admission": scenario["admission"],
        "listed": scenario["listed"],
        "counters": {key: int(value) for key, value in scenario["counters"].items()},
        "policy": None,
        "outcome": "rejected",
        "reason": None,
        "records": [],
    }

    cfg = declaration["scenarios"].get(name)
    if cfg is None:
        return _reject(entry, "no reviewed producer configuration for this scenario")
    policy = {"name": cfg["policy"]["name"], "deep_hooks": sorted(cfg["policy"]["deep_hooks"])}
    entry["policy"] = {"name": policy["name"], "sha256": policy_digest(policy),
                       "deep_hooks": policy["deep_hooks"],
                       # The digest names the declared policy; the export cannot attest
                       # that the producer actually applied it.
                       "attested": False}

    if scenario["admission"] != "ok":
        return _reject(entry, "admission rejected by the capture")
    if not scenario["listed"]:
        entry["outcome"] = "withheld"
        entry["reason"] = "the producer deliberately printed no observations"
        return entry

    observations = scenario["observations"]
    for observation in observations:
        hook_id = str(observation["hook_id"])
        if hook_id not in declaration["hooks"]:
            return _reject(entry, f"hook {hook_id} is not declared")
        if observation["hook_id"] not in policy["deep_hooks"]:
            return _reject(
                entry, f"hook {hook_id} was printed but the declared policy excludes it")
    try:
        registry.verify_closure(observations)
    except registry.RegistryError as exc:
        return _reject(entry, f"closure failed: {exc}")

    lifetime_size = _creating_sizes(observations, declaration)
    try:
        payloads = [_payload(observation, name, declaration, entry["policy"],
                             lifetime_size, store)
                    for observation in observations]
    except (MaterializeError, registry.RegistryError) as exc:
        return _reject(entry, str(exc))
    for payload in payloads:
        digest = store.add_record(payload)
        gaps = schema.qualification_gaps(payload)
        native = payload["completeness"]["state"]
        semantic = ("incomplete" if native == "incomplete"
                    else "domain-incomplete" if gaps else "qualified")
        entry["records"].append({
            "digest": digest,
            "native_state": native,
            "semantic_state": semantic,
            "gaps": list(gaps),
            "resources": _bound_resources(payload),
        })
    entry["outcome"] = "materialized"
    return entry


def _reject(entry: dict[str, Any], reason: str) -> dict[str, Any]:
    entry["outcome"] = "rejected"
    entry["reason"] = reason
    entry["records"] = []
    return entry


def _creating_sizes(observations: Iterable[dict[str, Any]],
                    declaration: dict[str, Any]) -> dict[tuple[int, int], int]:
    """Independent version sizes: a creating call's declared size, per lifetime.

    The size deliberately does not come from the snapshot it is checking. A producer
    that creates the resource in this capture states it; otherwise the reviewed
    declaration's producer constant is the only independent source, and an absent one
    makes the scenario unattributable.
    """
    sizes: dict[tuple[int, int], int] = {}
    default = declaration.get("resource_size")
    # shortcut: sizes are keyed by lifetime only. The synthetic subject's concurrent
    # worlds all create the same size; a producer whose threads create different
    # sizes needs the world identity in this key.
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
        sizes[(origin.slot, origin.generation)] = int(observation["args"][0])
    if default is not None:
        sizes.setdefault((-1, -1), default)
    return sizes


def _size_for(lifetime: registry.Reference, sizes: dict[tuple[int, int], int]) -> int | None:
    return sizes.get((lifetime.slot, lifetime.generation), sizes.get((-1, -1)))


def _payload(observation: dict[str, Any], scenario: str, declaration: dict[str, Any],
             policy: dict[str, Any], sizes: dict[tuple[int, int], int],
             store: EvidenceStore) -> dict[str, Any]:
    hook = declaration["hooks"][str(observation["hook_id"])]
    carriers, missing, abi_confidence = _carriers(hook, observation)
    version, span_meta = _version(observation, sizes, store)
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
    deps = _dependencies(observation)
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
            "call_id": f"{declaration['capture_id']}:{scenario}:{thread}:{observation['sequence']}",
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
            "unknown_dependencies": missing if native == "captured" else [],
        },
        "resources": resources,
        "effects": {
            "ordered": [],
            "complete": declaration["effects"] == "none",
        },
        "completeness": {
            "state": native,
            "losses": 0,
            "unavailable_dependencies": deps,
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
            "context": list(observation["context"]),
            "origin": list(observation["origin"]),
            "content": span_meta,
        },
    }


def _carriers(hook: dict[str, Any], observation: dict[str, Any]
              ) -> tuple[list[dict[str, Any]], list[str], str]:
    """Declared ABI carriers, with absent values marked unknown rather than zero."""
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
    return carriers, missing, ("observed" if not missing else "unknown")


def _version(observation: dict[str, Any], sizes: dict[tuple[int, int], int],
             store: EvidenceStore) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
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
        return None, meta
    if any(span["status"] != "captured" for span in spans):
        raise MaterializeError(
            f"observation {observation['sequence']} is complete but declares a lost span")
    if not spans:
        return None, meta
    try:
        origin = registry.reference_from_context(observation["origin"])
    except registry.RegistryError as exc:
        raise MaterializeError(f"content has no usable origin: {exc}") from exc
    size = _size_for(origin, sizes)
    if size is None:
        raise MaterializeError(
            f"no independent size for {origin.named} (observation "
            f"{observation['sequence']})")
    data = b"".join(span["bytes"] for span in spans)
    if len(data) != size:
        raise MaterializeError(
            f"resource version {origin.named} v{origin.version} is {len(data)} bytes "
            f"but its declared size is {size}")
    digest = store.put_blob(data)
    return {
        "resource_id": f"slot:{origin.slot}",
        "generation": origin.generation,
        "version": origin.version,
        "contents_sha256": digest,
    }, meta


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
