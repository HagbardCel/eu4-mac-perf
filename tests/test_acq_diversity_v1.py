"""Adversarial checks for the independent acq-diversity/v1 model."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))
sys.path.insert(0, str(ROOT))

import acq_diversity_v1 as acq  # noqa: E402
from oracle.certify.policy import behavior  # noqa: E402
from oracle.function import fixture  # noqa: E402

POLICY = ROOT / "docs/oracle/policies/gfx-set-constant-buffer.json"


def _qualified(text: str, descriptor: dict) -> None:
    body = fixture.materialize_capture(text, descriptor)
    if body["manifest"]["verdict"] != "qualified":
        raise AssertionError(body["manifest"])
    if body["record"]["completeness"]["unavailable_dependencies"]:
        raise AssertionError(body["record"]["completeness"])
    if body["record"]["completeness"]["losses"]:
        raise AssertionError("losses")


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = fixture.load_descriptor()
        cls.s03 = acq.s03_text(cls.descriptor)
        _qualified(cls.s03, cls.descriptor)

    def test_lower_bound_contract_example(self) -> None:
        self.assertEqual(acq.lower_bound(set(acq.REQUIRED)), 6)
        self.assertEqual(acq.lower_bound(
            {"slot:3", "selector:fixed-length", "input:zero"}), 2)

    def test_constructed_dword_and_zero_are_qualified(self) -> None:
        for dword in (1, 0):
            text = acq.build_transcript(
                self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=dword)
            _qualified(text, self.descriptor)

    def test_identical_calls_100(self) -> None:
        session = acq.Session(self.descriptor)
        for _ in range(100):
            session.invoke(self.s03)
        got = session.snapshot()
        self.assertTrue(session.identities_hold())
        self.assertEqual(got["intercepted"], 100)
        self.assertEqual(got["novel_retained"], 1)
        self.assertEqual(got["producer_matched_repeat"], 7)
        self.assertEqual(got["screened_repeat"], 92)
        self.assertEqual(got["selected"], 8)
        self.assertEqual(session.entries[0].state, "ANCHORED")
        line = acq.run_shared("identical_calls_100")
        self.assertIn("scenario=identical_calls_100", line)
        self.assertNotIn("outcome=", line)
        self.assertIn("intercepted=100", line)

    def test_same_slot_different_dword(self) -> None:
        other = acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=1)
        _qualified(other, self.descriptor)
        self.assertNotEqual(acq._screening_key(self.s03, self.descriptor),
                            acq._screening_key(other, self.descriptor))
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        session.invoke(other)
        self.assertEqual(session.snapshot()["novel_retained"], 2)
        line = acq.run_shared("same_slot_different_dword")
        self.assertIn("novel_retained=2", line)

    def test_phase_b_refusal_keeps_e(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        outcome = session.invoke(self.s03, fault="admission_cas_fail")
        got = session.snapshot()
        self.assertEqual(outcome, "cas_exhausted")
        self.assertEqual(session.entries[0].e, 2)
        self.assertEqual(got["selected"], 1)
        self.assertEqual(got["cas_exhausted"], 1)
        self.assertFalse(session.entries[0].verify_held)
        line = acq.run_shared("scheduled_sample_phase_b_refused")
        self.assertIn("variant=cas_exhausted", line)
        self.assertIn("outcome=cas_exhausted", line)

    def test_missing_cell_reprobe_is_pcov_until_retain(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03, fault="ineligible")
        self.assertEqual(session.established, set())
        self.assertEqual(session.entries[0].state, "UNANCHORED")
        # The next eligible encounter is the scheduled reprobe and reserves P_cov.
        session.buffers = 0
        refused = session.invoke(self.s03)
        self.assertEqual(refused, "capture_buffer_refused")
        self.assertEqual(session.entries[0].u, 2)
        self.assertEqual(session.snapshot()["selected"], 1)
        session.buffers = acq.QUOTA
        # Rebuild a clean reprobe so the reservation is observable before commit.
        session = acq.Session(self.descriptor)
        session.invoke(self.s03, fault="ineligible")
        session.invoke(self.s03, fault="admission_cas_fail")
        self.assertEqual(session.established, set())
        self.assertEqual(session.snapshot()["cas_exhausted"], 1)
        session.invoke(self.s03)
        self.assertTrue(session.established)
        self.assertEqual(session.r, 1)
        line = acq.run_shared("unanchored_missing_cell_pcov")
        self.assertIn("novel_retained=1", line)

    def test_two_thread_barriers(self) -> None:
        held = acq.Session(self.descriptor)
        held.gate_held = True
        self.assertEqual(held.invoke(self.s03), "cas_exhausted")
        self.assertTrue(all(entry.state == "EMPTY" for entry in held.entries))
        self.assertEqual(held.snapshot()["e"] if False else held.p_cov, 0)

        pending = acq.Session(self.descriptor)
        pending.invoke(self.s03, fault="leave_pending")
        self.assertEqual(pending.invoke(self.s03), "pending_contended")
        self.assertEqual(sum(entry.state == "PENDING" for entry in pending.entries), 1)

        raced = acq.Session(self.descriptor)
        raced.pause_before_gate(self.s03)
        raced.invoke(self.s03, fault="leave_pending")
        self.assertEqual(raced.complete_paused(), "key_insert_contended")
        self.assertEqual(sum(entry.state == "PENDING" for entry in raced.entries), 1)
        self.assertEqual(raced.snapshot()["key_insert_contended"], 1)
        self.assertEqual(raced.p_cov + raced.p_div, 1)
        line = acq.run_shared("two_threads_same_unseen_key", "key_insert_contended")
        self.assertIn("variant=key_insert_contended", line)
        self.assertIn("outcome=key_insert_contended", line)

    def test_anchor_survives_nonretained(self) -> None:
        line = acq.run_shared("anchor_survives_nonretained", "encounter_33")
        self.assertIn("variant=encounter_33", line)
        self.assertIn("distinct_not_retained=1", line)
        self.assertIn("ineligible_not_established=1", line)
        self.assertIn("storage_failed=1", line)
        self.assertIn("novel_retained=1", line)

    def test_rollback_fail_is_not_selected(self) -> None:
        session = acq.Session(self.descriptor)
        outcome = session.invoke(self.s03, fault="rollback_fail")
        self.assertEqual(outcome, "cas_exhausted")
        self.assertTrue(session.admission_lost)
        self.assertEqual(session.snapshot()["selected"], 0)
        self.assertEqual(session.r, 0)
        line = acq.run_shared("rollback_fail_after_bind_fail")
        self.assertIn("admission_invariant_lost=1", line)
        self.assertIn("selected=0", line)

    def test_pre_state_change_is_not_a_producer_match(self) -> None:
        mutated = acq._mutate_canary(self.s03)
        _qualified(mutated, self.descriptor)
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        outcome = session.invoke(mutated)
        self.assertEqual(outcome, "distinct_not_retained")
        self.assertEqual(session.entries[0].state, "ANCHORED")
        self.assertEqual(session.r, 1)

    def test_post_state_word_count_is_distinct(self) -> None:
        other = acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=13, words=8)
        _qualified(other, self.descriptor)
        self.assertEqual(acq._screening_key(self.s03, self.descriptor),
                         acq._screening_key(other, self.descriptor))
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        outcome = session.invoke(other)
        self.assertNotEqual(outcome, "producer_matched_repeat")
        self.assertNotEqual(outcome, "audit_persisted")
        self.assertEqual(session.entries[0].state, "ANCHORED")

    def test_zero_after_nonzero_does_not_exhaust_coverage(self) -> None:
        nonzero = acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=1)
        zero = acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=0)
        _qualified(nonzero, self.descriptor)
        _qualified(zero, self.descriptor)
        session = acq.Session(self.descriptor)
        for _ in range(32):
            session.invoke(nonzero)
        session.invoke(zero)
        self.assertLessEqual(session.r, 4)
        self.assertTrue(session.identities_hold())
        self.assertIn("input:zero", session.established)

    def test_unreadable_scan_publishes_nothing(self) -> None:
        session = acq.Session(self.descriptor)
        session.unreadable.add(7)
        self.assertEqual(session.invoke(self.s03), "cas_exhausted")
        self.assertTrue(all(entry.state == "EMPTY" for entry in session.entries))
        self.assertEqual(session.snapshot()["selected"], 0)

    def test_reprobe_blocks_reclamation(self) -> None:
        session = acq.Session(self.descriptor)
        session.inject_unanchored()
        kept = session.entries[0].key
        session.entries[0].reprobe_held = True
        other = acq.build_transcript(
            self.descriptor, slot=4, r9d=9, buffer_dword=None)
        _qualified(other, self.descriptor)
        session.invoke(other)
        self.assertEqual(session.entries[0].key, kept)
        self.assertEqual(session.entries[0].state, "UNANCHORED")

    def test_reclaim_lowest_idle_unanchored(self) -> None:
        session = acq.Session(self.descriptor)
        session.inject_unanchored()
        session.invoke(self.s03)
        self.assertEqual(session.entries[0].state, "ANCHORED")
        self.assertEqual(session.entries[0].key,
                         acq._screening_key(self.s03, self.descriptor))
        self.assertGreaterEqual(session.snapshot()["unanchored_reclaimed"], 1)

    def test_pending_full_table_is_synthetic_injection(self) -> None:
        session = acq.Session(self.descriptor)
        session.inject_pending_full()
        self.assertEqual(session.invoke(self.s03), "table_capacity_refused")

    def test_eight_unstable_reads(self) -> None:
        session = acq.Session(self.descriptor)
        session.unstable_reads = 8
        self.assertEqual(session.invoke(self.s03), "signature_read_contended")
        self.assertEqual(session.snapshot()["screened_repeat"], 0)

    def test_torn_read_is_not_a_mixed_snapshot(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        session.entries[0].generation = 1
        self.assertEqual(session.invoke(self.s03), "signature_read_contended")

    def test_ineligible_does_not_establish_then_second_reserves(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03, fault="ineligible")
        self.assertEqual(session.established, set())
        self.assertEqual(session.invoke(self.s03, fault="hold_reservation"), "held")
        self.assertEqual(session.p_cov, 1)
        self.assertEqual(session.established, set())

    def test_post_bind_keeps_prior_anchor(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        anchor = session.entries[0].anchor
        outcome = session.invoke(self.s03, fault="post_bind_mismatch")
        self.assertEqual(outcome, "selected_invariant_failure")
        self.assertTrue(session.admission_lost)
        self.assertEqual(session.entries[0].state, "ANCHORED")
        self.assertEqual(session.entries[0].anchor, anchor)
        self.assertEqual(len(session.retained_texts), 1)

    def test_missing_reservation_after_selection(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        self.assertEqual(
            session.invoke(self.s03, fault="missing_reservation"),
            "selected_invariant_failure")

    def test_sixty_four_retained_does_not_overwrite(self) -> None:
        session = acq.Session(self.descriptor)
        fixed = acq.build_transcript(
            self.descriptor, slot=3, r9d=13, buffer_dword=None)
        _qualified(fixed, self.descriptor)
        session.invoke(fixed)
        session.invoke(acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=0, words=1))
        for index in range(62):
            session.invoke(acq.build_transcript(
                self.descriptor, slot=index % 6, r9d=acq.BUF,
                buffer_dword=index + 1, words=index + 1))
        self.assertEqual(session.r, 64)
        self.assertEqual(session.l_value(), 0)
        extra = acq.build_transcript(
            self.descriptor, slot=1, r9d=acq.BUF, buffer_dword=200)
        before = [entry.key for entry in session.entries]
        session.invoke(extra)
        self.assertEqual(session.r, 64)
        self.assertEqual([entry.key for entry in session.entries], before)

    def test_anchored_repeat_does_not_touch_admission(self) -> None:
        session = acq.Session(self.descriptor)
        session.invoke(self.s03)
        session.invoke(self.s03)  # e == 2, a real verification
        session.reserved.update(set(acq.REQUIRED) - session.established)
        before = (session.p_cov, session.p_div, session.buffers)
        self.assertEqual(session.invoke(self.s03), "screened_repeat")
        self.assertEqual((session.p_cov, session.p_div, session.buffers), before)

    def test_failed_pending_can_retry(self) -> None:
        session = acq.Session(self.descriptor)
        self.assertEqual(session.invoke(self.s03, fault="incomplete"),
                         "candidate_incomplete")
        self.assertEqual(session.entries[0].state, "EMPTY")
        self.assertEqual(session.invoke(self.s03), "novel_retained")

    def test_coverage_claim_is_not_preempted_by_the_gate(self) -> None:
        session = acq.Session(self.descriptor)
        session.inject_unanchored(1)
        kept = session.entries[0].key
        session.entries[0].state = "ANCHORED"
        session.entries[0].reprobe_held = False
        other = acq.build_transcript(
            self.descriptor, slot=3, r9d=4, buffer_dword=None)
        _qualified(other, self.descriptor)
        session.gate_held = True
        session.invoke(other)
        self.assertEqual(session.entries[0].key, kept)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = fixture.load_descriptor()

    def _seal(self, root: Path, *, established: str, counters: dict[str, int],
              captures: list[tuple[str, str]], audit_entries: list[dict] | None = None,
              break_counter: bool = False) -> None:
        (root / "captures").mkdir(exist_ok=True)
        files = {}
        for name, text in captures:
            path = root / name
            path.write_text(text, encoding="utf-8")
            files[name] = sha256(path.read_bytes()).hexdigest()
        audit = {
            "schema": acq.AUDIT_SCHEMA,
            "entries": audit_entries or [],
        }
        audit_path = root / "audit-associations.json"
        audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
        files["audit-associations.json"] = sha256(audit_path.read_bytes()).hexdigest()
        manifest = {"schema": acq.MANIFEST_V2, "session": "acq-v2", "files": files}
        (root / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8")
        fields = {name: str(counters.get(name, 0)) for name in acq.DIAG_FIELDS}
        if break_counter:
            fields["intercepted"] = "0"
        lines = [acq.SUMMARY_V2, "session=acq-v2", f"acquisition_policy={acq.POLICY_NAME}",
                 "state=sealed", "quiescence=ok", f"established={established}"]
        lines.extend(f"{key}={value}" for key, value in fields.items())
        lines.append("END")
        (root / "summary.native").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _one_retain_counters(self) -> dict[str, int]:
        session = acq.Session(self.descriptor)
        session.invoke(acq.s03_text(self.descriptor))
        return session.snapshot()

    def test_empty_audit_admits_and_v2_report_dispatches(self) -> None:
        text = acq.s03_text(self.descriptor)
        session = acq.Session(self.descriptor)
        session.invoke(text)
        root = Path(tempfile.mkdtemp(prefix="acq-v2-"))
        self.addCleanup(lambda: shutil.rmtree(root, True))
        established = ",".join(sorted(session.established))
        self._seal(root, established=established, counters=session.snapshot(),
                   captures=[("captures/acq-v2.0.capture", text)])
        self.assertEqual(acq.structural_problems(root), [])
        import eu4_gfx_live_capture as live
        report = live.report(root)
        self.assertEqual(report["schema"], "eu4-gfx-live-session/v2")
        self.assertEqual(report["structural"], "admitted")

    def test_broken_counters_and_audit_mismatch_are_refused(self) -> None:
        text = acq.s03_text(self.descriptor)
        other = acq.build_transcript(
            self.descriptor, slot=2, r9d=acq.BUF, buffer_dword=1, words=8)
        root = Path(tempfile.mkdtemp(prefix="acq-v2-bad-"))
        self.addCleanup(lambda: shutil.rmtree(root, True))
        counters = self._one_retain_counters()
        self._seal(root, established="slot:2", counters=counters,
                   captures=[("captures/acq-v2.0.capture", text)],
                   break_counter=True)
        self.assertTrue(acq.structural_problems(root))
        print("ACQ_VERIFY scenario=broken_summary_counters verdict=refused")

        root2 = Path(tempfile.mkdtemp(prefix="acq-v2-audit-"))
        self.addCleanup(lambda: shutil.rmtree(root2, True))
        session = acq.Session(self.descriptor)
        session.invoke(text)
        audit_name = "captures/acq-v2.0.audit.capture"
        retained = "captures/acq-v2.0.capture"
        row = {
            "audit_path": audit_name,
            "audit_sha256": sha256(other.encode()).hexdigest(),
            "reference_path": retained,
            "reference_sha256": sha256(text.encode()).hexdigest(),
        }
        self._seal(root2, established=",".join(sorted(session.established)),
                   counters=session.snapshot(),
                   captures=[(retained, text), (audit_name, other)],
                   audit_entries=[row])
        self.assertTrue(any("producer" in item or "qualified" in item
                            for item in acq.structural_problems(root2)))
        print("ACQ_VERIFY scenario=audit_hash_a2_mismatch verdict=refused")

    def test_manifest_audit_and_invariant_refusals(self) -> None:
        text = acq.s03_text(self.descriptor)
        session = acq.Session(self.descriptor)
        session.invoke(text)
        root = Path(tempfile.mkdtemp(prefix="acq-v2-refuse-"))
        self.addCleanup(lambda: shutil.rmtree(root, True))
        established = ",".join(sorted(session.established))
        counters = session.snapshot()
        self._seal(root, established=established, counters=counters,
                   captures=[("captures/acq-v2.0.capture", text)])
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["audit-associations.json"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(any("hash" in item for item in acq.structural_problems(root)))

        lost = dict(counters)
        lost["admission_invariant_lost"] = 1
        root2 = Path(tempfile.mkdtemp(prefix="acq-v2-lost-"))
        self.addCleanup(lambda: shutil.rmtree(root2, True))
        self._seal(root2, established=established, counters=lost,
                   captures=[("captures/acq-v2.0.capture", text)])
        self.assertTrue(any("nonzero" in item for item in acq.structural_problems(root2)))

        root3 = Path(tempfile.mkdtemp(prefix="acq-v2-dup-"))
        self.addCleanup(lambda: shutil.rmtree(root3, True))
        row = {
            "audit_path": "captures/missing.audit.capture",
            "audit_sha256": "ab" * 32,
            "reference_path": "captures/outside.capture",
            "reference_sha256": "cd" * 32,
        }
        self._seal(root3, established=established, counters=counters,
                   captures=[("captures/acq-v2.0.capture", text)],
                   audit_entries=[row, row])
        problems = acq.structural_problems(root3)
        self.assertTrue(any("duplicate" in item or "outside" in item for item in problems))

    def test_overclaimed_cell_is_refused(self) -> None:
        text = acq.s03_text(self.descriptor)
        session = acq.Session(self.descriptor)
        session.invoke(text)
        root = Path(tempfile.mkdtemp(prefix="acq-v2-cell-"))
        self.addCleanup(lambda: shutil.rmtree(root, True))
        self._seal(root, established="slot:0", counters=session.snapshot(),
                   captures=[("captures/acq-v2.0.capture", text)])
        self.assertTrue(any("coverage" in item for item in acq.structural_problems(root)))

    def test_certificate_is_not_a_stronger_tier(self) -> None:
        text = acq.s03_text(self.descriptor)
        session = acq.Session(self.descriptor)
        session.invoke(text)
        root = Path(tempfile.mkdtemp(prefix="acq-v2-cert-"))
        cert_dir = Path(tempfile.mkdtemp(prefix="acq-v2-out-"))
        self.addCleanup(lambda: shutil.rmtree(root, True))
        self.addCleanup(lambda: shutil.rmtree(cert_dir, True))
        self._seal(root, established=",".join(sorted(session.established)),
                   counters=session.snapshot(),
                   captures=[("captures/acq-v2.0.capture", text)])
        candidate = {
            "source_sha256": "0" * 64,
            "executable_sha256": "0" * 64,
            "build_identity": "acq-2-test",
            "build_flags": [],
            "harness_path": str(ROOT / "missing-harness"),
        }
        body = acq.certify_e1_v2(
            root, self.descriptor, POLICY, candidate, certificate_dir=cert_dir)
        self.assertEqual(body["structural"], "admitted")
        certificate = body["certificate"]
        self.assertEqual(certificate["schema"], "eu4-oracle-tier1-certificate/v1")
        self.assertNotEqual(certificate["evidence_tier"], "tier1-live-sealed-session")
        self.assertFalse(certificate["evidence"]["live_grounded"])
        self.assertNotEqual(certificate["promotion"]["status"], "certified")
        record = fixture.materialize_capture(text, self.descriptor)["record"]
        self.assertIn("slot:2", behavior(record, self.descriptor).cells)


if __name__ == "__main__":
    unittest.main()
