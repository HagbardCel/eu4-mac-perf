"""P4.10 (A1): materializing a P4.9 export into honest, closed evidence.

The frozen P4.9 export is the subject: it is materialized byte-for-byte into
schema-valid records, whole resource-version blobs and a sealed capture binding.
The tests pin the three corrections that made A1 reviewable:

* native capture completeness and semantic qualification are reconciled, not
  conflated - a fully captured observation outside the declared domain stays stored
  and accounted for but never receives a semantic digest;
* one blob is published per *complete resource version*, assembled from its spans in
  declaration order and checked against an independent size, and the binding's own
  verifier - not ``EvidenceStore.verify`` - establishes the blob-to-record relation;
* invocation identity includes the capture and scenario, so thread sequences may
  repeat without becoming ambiguous.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from oracle.recorder import harness, materialize, schema
from oracle.recorder.store import EvidenceStore, StoreError

DECLARATION = (Path(__file__).resolve().parents[1]
               / "oracle" / "recorder" / "declarations" / "content.json")


def _scenario(binding: dict, name: str) -> dict:
    return next(entry for entry in binding["scenarios"] if entry["name"] == name)


def _observation(seq: int, hook: int, *, args=(), context=(1, 0, 0, 1, 0, 0),
                 spans=(), origin=None, complete=1, fault="none") -> dict:
    """One export observation plus the native counters it implies."""
    items: list[str] = []
    declared = captured = lost = spans_lost = 0
    for span in spans:
        if isinstance(span, bytes):
            items.append(f"c:{len(span)}:{span.hex()}")
            declared += len(span)
            captured += len(span)
        else:
            items.append(f"l:{span}")
            declared += span
            lost += span
            spans_lost += 1
    if origin is None:
        origin = context if spans else ()
    line = (
        f"OBS thread=0 seq={seq} phase=0 hook={hook} point=0 result=0 "
        f"complete={complete} fault={fault} nargs={len(args)} "
        f"args={','.join(str(arg) for arg in args)} ncontext={len(context)} "
        f"context={','.join(str(word) for word in context)} nspans={len(spans)} "
        f"content={','.join(items)} norigin={len(origin)} "
        f"origin={','.join(str(word) for word in origin)}")
    return {"line": line, "complete": complete, "fault": fault, "declared": declared,
            "captured": captured, "lost": lost, "spans_lost": spans_lost}


def _export(observations: list[dict], *, name="probe", admission="ok",
            rejected=0, listed=1) -> str:
    complete = sum(1 for obs in observations if obs["complete"] == 1)
    incomplete = len(observations) - complete
    declared = sum(obs["declared"] for obs in observations)
    captured = sum(obs["captured"] for obs in observations)
    lost = sum(obs["lost"] for obs in observations)
    spans_lost = sum(obs["spans_lost"] for obs in observations)
    counters = (
        f"intercepted={len(observations)} selected={len(observations)} unselected=0 "
        f"captured={complete} incomplete={incomplete} lost=0 reentrant=0 "
        f"rejected={rejected} content_bytes_declared={declared} "
        f"content_bytes_captured={captured} content_bytes_lost={lost} "
        f"content_spans_lost={spans_lost}")
    lines = ["RECORDER-EXPORT v1", f"SCENARIO {name}", f"COUNTERS {counters}",
             "ACCOUNTING ok", f"ADMISSION {admission}",
             "ARENA capacity=64 used=8 intervals=2 overlaps=0 out_of_range=0",
             f"OBSERVATIONS count={len(observations)} listed={listed}"]
    lines.extend(obs["line"] for obs in observations)
    lines.append(f"END {counters} effects=0")
    return "\n".join(lines) + "\n"


def _create(size: int = 4) -> dict:
    return _observation(1, 1, args=(size,), spans=[b"\x00" * size])


def _effects_free(declaration: dict) -> dict:
    """Exercise the classifier's qualified path on the frozen evidence.

    The frozen producer really does emit one external effect per call; this override
    is a mechanism test and is never shipped as a claim about the producer.
    """
    copy = json.loads(json.dumps(declaration))
    copy["effects"] = "none"
    return copy


class FrozenExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.declaration = materialize.load_declaration(DECLARATION)
        cls.text = harness.golden_transcript("content")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = EvidenceStore(Path(cls._tmp.name))
        cls.binding = materialize.materialize_export(cls.text, cls.declaration, cls.store)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_frozen_export_materializes_into_verified_evidence(self):
        verification = materialize.verify_binding(
            self.store, self.binding, export_text=self.text, declaration=self.declaration)
        self.assertEqual(verification["mode"], "source-bound")
        self.assertTrue(verification["admitted"])
        digest = verification["binding_sha256"]
        self.assertEqual(digest, materialize.binding_digest(self.binding))
        self.store.verify()
        self.assertIn(digest, self.store.manifest()["payload"]["blobs"])
        self.assertEqual(len(self.binding["scenarios"]), 12)

    def test_native_capture_completeness_is_not_semantic_qualification(self):
        # The P4.9 producer exports external effects only as an aggregate counter, so
        # every completely captured observation is stored and accounted for but lies
        # outside the declared semantic domain.
        entry = _scenario(self.binding, "content-ample")
        self.assertEqual(entry["counters"]["captured"], 6)
        self.assertEqual(entry["counters"]["incomplete"], 2)
        self.assertEqual(len(entry["records"]), 8)
        for meta in entry["records"]:
            self.assertIn(meta["digest"], self.store.records)
            payload = self.store.get_record(meta["digest"])
            self.assertEqual(payload["completeness"]["state"], meta["native_state"])
            if meta["native_state"] == "captured":
                self.assertEqual(meta["semantic_state"], "domain-incomplete")
                self.assertIn("effects-incomplete", meta["gaps"])

        materialized = [sc for sc in self.binding["scenarios"]
                        if sc["outcome"] == "materialized"]
        captured = sum(sc["counters"]["captured"] for sc in materialized)
        incomplete = sum(sc["counters"]["incomplete"] for sc in materialized)
        ledger = self.binding["ledger"]
        self.assertEqual(ledger["unique"], 0)
        self.assertEqual(ledger["verified_repeat"], 0)
        self.assertEqual(ledger["groups"], {})
        self.assertEqual(ledger["domain_incomplete"], captured)
        self.assertEqual(ledger["domain_incomplete_gaps"],
                         {"effects-incomplete": captured, "unknown-input": 2})
        self.assertEqual(ledger["incomplete"], incomplete)
        self.assertEqual(ledger["counted_only"] + captured + incomplete + ledger["lost"],
                         ledger["intercepted"])

    def test_a_complete_observation_with_undeclared_effects_gets_no_semantic_digest(self):
        entry = _scenario(self.binding, "content-exact")
        meta = next(m for m in entry["records"] if m["native_state"] == "captured")
        payload = self.store.get_record(meta["digest"])
        self.assertTrue(schema.validate_payload(payload) is None)
        self.assertFalse(schema.qualifies_for_semantic(payload))
        self.assertEqual(schema.qualification_gaps(payload), ("effects-incomplete",))
        with self.assertRaises(schema.SchemaError):
            schema.semantic_digest(payload)

    def test_every_whole_resource_version_is_one_verified_blob(self):
        # The golden's `content-ample` captures three versions of slot:0@0: create
        # (00000000), update (05060708), update (06080708). Each is a whole version,
        # even when the native recorder assembled it from more than one span.
        versions = {}
        for meta in _scenario(self.binding, "content-ample")["records"]:
            for entry in meta["resources"]:
                versions[entry["contents_sha256"]] = entry
        self.assertEqual(len(versions), 3)
        for digest, entry in versions.items():
            blob = self.store.get_blob(digest)
            self.assertEqual(len(blob), entry["size"])
            self.assertEqual(sha256(blob).hexdigest(), digest)
            self.assertEqual(sum(span["length"] for span in entry["spans"]), entry["size"])
            offset = 0
            for span in entry["spans"]:
                self.assertEqual(sha256(blob[offset:offset + span["length"]]).hexdigest(),
                                 span["sha256"])
                offset += span["length"]

    def test_each_concurrent_world_owns_a_distinct_resource(self):
        # Four worker threads initialize independent worlds and reuse slot 0 and
        # generation 0. Their local identifiers coincide; their resources do not.
        entry = _scenario(self.binding, "content-concurrent")
        resource_ids = {meta["resources"][0]["resource_id"]
                        for meta in entry["records"] if meta["resources"]}
        self.assertEqual(resource_ids, {
            f"world:content-golden/content-concurrent/{thread}:slot:0"
            for thread in range(4)})

        # A scoped version maps to exactly one content across the whole binding.
        contents: dict[tuple[str, int, int], str] = {}
        for scenario in self.binding["scenarios"]:
            for meta in scenario["records"]:
                for resource in meta["resources"]:
                    key = (resource["resource_id"], resource["generation"],
                           resource["version"])
                    previous = contents.setdefault(key, resource["contents_sha256"])
                    self.assertEqual(previous, resource["contents_sha256"], key)

    def test_scenarios_do_not_collide_on_the_same_local_resource_version(self):
        # `content-ample` and `content-concurrent` thread 0 both have slot 0,
        # generation 0, version 1 -- and different bytes (seed 5 vs seed 1).
        def version_one(scenario: str, thread: int) -> dict:
            for meta in _scenario(self.binding, scenario)["records"]:
                payload = self.store.get_record(meta["digest"])
                if payload["provenance"]["thread"] != thread:
                    continue
                for resource in meta["resources"]:
                    if resource["generation"] == 0 and resource["version"] == 1:
                        return resource
            raise AssertionError(f"{scenario}/{thread} has no version 1")

        ample = version_one("content-ample", 0)
        concurrent = version_one("content-concurrent", 0)
        self.assertEqual((ample["generation"], ample["version"]), (0, 1))
        self.assertEqual((concurrent["generation"], concurrent["version"]), (0, 1))
        self.assertNotEqual(ample["resource_id"], concurrent["resource_id"])
        self.assertNotEqual(ample["contents_sha256"], concurrent["contents_sha256"])
        self.assertNotEqual(self.store.get_blob(ample["contents_sha256"]),
                            self.store.get_blob(concurrent["contents_sha256"]))

    def test_a_complete_two_span_version_assembles_in_declaration_order(self):
        observations = [_create(4),
                        _observation(2, 6, args=(0,), context=(1, 0, 0, 1, 0, 1),
                                     spans=[b"\xaa\xbb", b"\xcc\xdd"],
                                     origin=(1, 0, 0, 1, 0, 1))]
        text = _export(observations, name="content-two-spans")
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(text, self.declaration, store)
            materialize.verify_binding(store, binding, export_text=text,
                                       declaration=self.declaration)
            entry = _scenario(binding, "content-two-spans")
            self.assertEqual(entry["outcome"], "materialized")
            pair = store.get_record(entry["records"][1]["digest"])
            version = pair["resources"][0]
            self.assertEqual(store.get_blob(version["contents_sha256"]), b"\xaa\xbb\xcc\xdd")
            self.assertEqual(
                [span["length"] for span in entry["records"][1]["resources"][0]["spans"]],
                [2, 2])

    def test_a_complete_version_shorter_than_its_independent_size_is_rejected(self):
        observations = [_create(4),
                        _observation(2, 6, args=(0,), context=(1, 0, 0, 1, 0, 1),
                                     spans=[b"\xaa\xbb", b"\xcc"],
                                     origin=(1, 0, 0, 1, 0, 1))]
        text = _export(observations, name="content-two-spans")
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(text, self.declaration, store)
        entry = _scenario(binding, "content-two-spans")
        self.assertEqual(entry["outcome"], "rejected")
        self.assertIn("declared size", entry["reason"])
        self.assertEqual(entry["records"], [])
        # Nothing from a rejected scenario is published, including the create blob
        # it produced before the bad version was derived.
        self.assertEqual(store.records, [])
        orphan = sha256(b"\x00" * 4).hexdigest()
        self.assertNotIn(orphan, store.manifest()["payload"]["blobs"])
        self.assertFalse(store.blob_path(orphan).exists())

    def test_a_complete_observation_that_declares_a_lost_span_is_rejected(self):
        observations = [_create(4),
                        _observation(2, 2, args=(0, 1, 2), context=(1, 0, 0, 1, 0, 1),
                                     spans=[b"\x00\x00", 2], origin=(1, 0, 0, 1, 0, 1))]
        text = _export(observations, name="content-two-spans")
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(text, self.declaration, store)
        entry = _scenario(binding, "content-two-spans")
        self.assertEqual(entry["outcome"], "rejected")
        self.assertIn("lost span", entry["reason"])

    def test_withheld_and_rejected_scenarios_keep_their_counters_and_no_records(self):
        malformed = _scenario(self.binding, "content-malformed")
        self.assertEqual(malformed["outcome"], "rejected")
        self.assertIn("admission", malformed["reason"])
        self.assertEqual(malformed["records"], [])
        self.assertGreater(malformed["counters"]["captured"], 0)

        stale = _scenario(self.binding, "content-stale-origin")
        self.assertEqual(stale["outcome"], "rejected")
        self.assertIn("closure failed", stale["reason"])
        self.assertEqual(stale["records"], [])

        withheld = _scenario(self.binding, "content-concurrent-bounded")
        self.assertEqual(withheld["outcome"], "withheld")
        self.assertFalse(withheld["listed"])
        self.assertEqual(withheld["records"], [])
        self.assertGreater(withheld["counters"]["captured"], 0)

    def test_invocation_ids_are_unique_across_scenarios_and_epochs(self):
        call_ids = []
        for scenario in self.binding["scenarios"]:
            for meta in scenario["records"]:
                payload = self.store.get_record(meta["digest"])
                call_id = payload["invocation"]["call_id"]
                self.assertTrue(call_id.startswith(
                    f"{self.declaration['capture_id']}:{scenario['name']}:"), call_id)
                call_ids.append(call_id)
        self.assertEqual(len(call_ids), len(set(call_ids)))
        # The same thread/sequence in two scenarios is not the same invocation.
        self.assertIn("content-golden:content-ample:0:1", call_ids)
        self.assertIn("content-golden:content-exact:0:1", call_ids)

    def test_the_declared_policy_and_profiles_are_the_effective_ones(self):
        policy = _scenario(self.binding, "content-policy")["policy"]
        self.assertEqual(policy["name"], "updates-only")
        self.assertEqual(policy["deep_hooks"], [2])
        self.assertEqual(policy["sha256"], materialize.policy_digest(
            {"name": "updates-only", "deep_hooks": [2]}))
        self.assertNotEqual(policy["sha256"], _scenario(
            self.binding, "content-ample")["policy"]["sha256"])
        for meta in _scenario(self.binding, "content-policy")["records"]:
            payload = self.store.get_record(meta["digest"])
            self.assertEqual(payload["provenance"]["capture_policy_digest"], policy["sha256"])

    def test_a_missing_declared_argument_is_unknown_input_not_observed_zero(self):
        entry = _scenario(self.binding, "content-zero-length")
        self.assertEqual(entry["counters"]["captured"], 2)
        read = next(meta for meta in entry["records"]
                    if self.store.get_record(meta["digest"])["raw"]["hook_id"] == 3)
        payload = self.store.get_record(read["digest"])
        self.assertEqual(payload["completeness"]["state"], "captured")
        self.assertEqual(read["semantic_state"], "domain-incomplete")
        self.assertIn("unknown-input:args:read:slot", read["gaps"])
        self.assertEqual(payload["abi"]["confidence"], "unknown")

    def test_the_binding_seal_detects_tampering_and_missing_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(self.text, self.declaration, store)

            tampered = json.loads(json.dumps(binding))
            tampered["export_sha256"] = "0" * 64
            with self.assertRaises(materialize.MaterializeError):
                materialize.verify_binding(store, tampered)

            tampered = json.loads(json.dumps(binding))
            entry = next(meta for scenario in tampered["scenarios"]
                         for meta in scenario["records"] if meta["resources"])
            entry["resources"][0]["contents_sha256"] = "0" * 64
            store.put_blob(schema.canonical_bytes(tampered))
            with self.assertRaises(materialize.MaterializeError):
                materialize.verify_binding(store, tampered)

            entry = next(meta for scenario in binding["scenarios"]
                         for meta in scenario["records"] if meta["resources"])
            store.blob_path(entry["resources"][0]["contents_sha256"]).unlink()
            with self.assertRaises(StoreError):
                materialize.verify_binding(store, binding)

    def test_structural_verification_is_inspection_not_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(self.text, self.declaration, store)
            entry = _scenario(binding, "content-exact")
            meta = next(m for m in entry["records"] if m["native_state"] == "captured")
            payload = json.loads(json.dumps(store.get_record(meta["digest"])))
            payload["abi"]["carriers"][0]["value_hex"] = "ff" * 8
            meta["digest"] = store.add_record(payload)
            store.put_blob(schema.canonical_bytes(binding))

            # The tampered record is internally consistent, so inspection passes ...
            inspection = materialize.verify_binding(store, binding)
            self.assertEqual(inspection["mode"], "structural")
            self.assertFalse(inspection["admitted"])

            # ... but it cannot be re-derived from the export, so it is not admitted.
            with self.assertRaises(materialize.MaterializeError):
                materialize.verify_binding(store, binding, export_text=self.text,
                                           declaration=self.declaration)
            with self.assertRaises(materialize.MaterializeError):
                materialize.verify_binding(store, binding, export_text=self.text)

    def test_a_ledger_that_does_not_rederive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(self.text, self.declaration, store)
            tampered = json.loads(json.dumps(binding))
            tampered["ledger"]["unique"] += 1
            store.put_blob(schema.canonical_bytes(tampered))
            with self.assertRaises(materialize.MaterializeError):
                materialize.verify_binding(store, tampered)

    def test_qualified_path_reconciles_repeats_without_rewriting_native_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(
                self.text, _effects_free(self.declaration), store)
            materialize.verify_binding(store, binding, export_text=self.text,
                                       declaration=_effects_free(self.declaration))
            # Two scenarios' identical creates observe different worlds; their
            # scoped resources must keep them from collapsing into one behavior.
            semantic = {}
            for name in ("content-ample", "content-exact"):
                meta = next(m for m in _scenario(binding, name)["records"]
                            if store.get_record(m["digest"])["raw"]["hook_id"] == 1)
                semantic[name] = schema.semantic_digest(store.get_record(meta["digest"]))
            self.assertNotEqual(semantic["content-ample"], semantic["content-exact"])
        ledger = binding["ledger"]
        self.assertGreater(ledger["unique"], 0)
        self.assertGreater(ledger["verified_repeat"], 0)
        self.assertEqual(ledger["domain_incomplete"], 1)
        self.assertEqual(ledger["domain_incomplete_gaps"], {"unknown-input": 2})
        self.assertTrue(any(len(ids) > 1 for ids in ledger["groups"].values()))

        # The counter statement is unchanged by qualification: the only difference is
        # where the captured observations are classified.
        native = _scenario(self.binding, "content-ample")["counters"]
        self.assertEqual(_scenario(binding, "content-ample")["counters"], native)
        self.assertEqual(ledger["unique"] + ledger["verified_repeat"],
                         ledger["intercepted"] - ledger["incomplete"]
                         - ledger["counted_only"] - ledger["lost"] - 1)

    def test_duplicate_reads_in_one_world_still_deduplicate(self):
        observations = [_create(4),
                        _observation(2, 3, args=(0, 3), context=(1, 0, 0, 1, 0, 1)),
                        _observation(3, 3, args=(0, 3), context=(1, 0, 0, 1, 0, 1))]
        text = _export(observations, name="content-exact")
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            declaration = _effects_free(self.declaration)
            binding = materialize.materialize_export(text, declaration, store)
            materialize.verify_binding(store, binding, export_text=text,
                                       declaration=declaration)
        ledger = binding["ledger"]
        # The create and one read are unique; the second identical read repeats.
        self.assertEqual(ledger["unique"], 2)
        self.assertEqual(ledger["verified_repeat"], 1)
        self.assertEqual(ledger["domain_incomplete"], 0)
        self.assertEqual(len(ledger["groups"]), 2)


class DeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.declaration = materialize.load_declaration(DECLARATION)

    def test_the_declaration_names_the_trusted_producer_sources(self):
        spec = harness.TARGETS["content"]
        parts = [f"{Path(path).name}:{sha256(Path(path).read_bytes()).hexdigest()}"
                 for path in (*spec["sources"], *spec["headers"])]
        parts.append("cflags:" + " ".join((*harness.COMPILE_FLAGS,
                                           *spec.get("cflags", ()))))
        expected = sha256("\n".join(sorted(parts)).encode()).hexdigest()
        self.assertEqual(self.declaration["module_sha256"], expected)

    def test_every_golden_scenario_has_a_reviewed_policy(self):
        parsed = harness.parse_export_transcript(harness.golden_transcript("content"))
        self.assertEqual({scenario["name"] for scenario in parsed["scenarios"]},
                         set(self.declaration["scenarios"]))
        for name, config in self.declaration["scenarios"].items():
            self.assertIn(config["policy"]["name"], ("deep", "updates-only"), name)

    def test_an_undeclared_scenario_is_rejected_not_silently_materialized(self):
        broken = json.loads(json.dumps(self.declaration))
        del broken["scenarios"]["content-off"]
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            binding = materialize.materialize_export(
                harness.golden_transcript("content"), broken, store)
            materialize.verify_binding(store, binding)
        entry = _scenario(binding, "content-off")
        self.assertEqual(entry["outcome"], "rejected")
        self.assertIn("no reviewed producer configuration", entry["reason"])
        self.assertEqual(entry["records"], [])

    def test_a_declaration_with_an_unknown_carrier_is_refused(self):
        broken = json.loads(json.dumps(self.declaration))
        broken["hooks"]["1"]["carriers"][0]["kind"] = "magic"
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_a_declaration_selecting_an_undeclared_hook_is_refused(self):
        broken = json.loads(json.dumps(self.declaration))
        broken["scenarios"]["content-ample"]["policy"]["deep_hooks"].append(99)
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_a_loaded_module_identity_is_refused_by_a1(self):
        broken = json.loads(json.dumps(self.declaration))
        broken["module_identity"] = "loaded-module"
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_the_resource_scope_must_be_declared_and_known(self):
        for scope in (None, "thread"):
            broken = json.loads(json.dumps(self.declaration))
            if scope is None:
                del broken["resource_scope"]
            else:
                broken["resource_scope"] = scope
            with self.assertRaises(materialize.MaterializeError):
                materialize.validate_declaration(broken)

    def test_every_hook_must_declare_its_arity(self):
        broken = json.loads(json.dumps(self.declaration))
        del broken["hooks"]["1"]["arity"]
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_a_carrier_may_not_exceed_the_declared_arity(self):
        broken = json.loads(json.dumps(self.declaration))
        broken["hooks"]["2"]["carriers"][0]["arg"] = 3
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_carriers_must_cover_every_declared_argument(self):
        broken = json.loads(json.dumps(self.declaration))
        broken["hooks"]["2"]["carriers"] = [
            carrier for carrier in broken["hooks"]["2"]["carriers"]
            if carrier["arg"] != 1]
        with self.assertRaises(materialize.MaterializeError):
            materialize.validate_declaration(broken)

    def test_an_extra_observed_argument_is_preserved_as_unknown_input(self):
        # A producer that passes more than the declared arity is not silently
        # truncated: the record stays stored, the bytes stay in `raw`, and the
        # observation is disqualified rather than given a narrow ABI.
        observations = [_observation(1, 1, args=(4, 99), spans=[b"\x00" * 4])]
        text = _export(observations, name="content-exact")
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            declaration = _effects_free(self.declaration)
            binding = materialize.materialize_export(text, declaration, store)
            entry = _scenario(binding, "content-exact")
            self.assertEqual(entry["outcome"], "materialized")
            meta = entry["records"][0]
            self.assertEqual(meta["semantic_state"], "domain-incomplete")
            self.assertIn("unknown-input:args:unexpected:create:1", meta["gaps"])
            payload = store.get_record(meta["digest"])
            self.assertEqual(payload["raw"]["args"], [4, 99])
            self.assertEqual(payload["state"]["unknown_dependencies"],
                             ["args:unexpected:create:1"])
            self.assertEqual(payload["abi"]["confidence"], "unknown")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
