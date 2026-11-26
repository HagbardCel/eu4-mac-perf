from pathlib import Path
import os
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

from automation import dsh_receipts, gfx_revision, qualification_sandbox
from automation.gfx_artifacts import sha256_file
from reverse_engineering import reconstruction_diff


class ProtectedDiagnosticTests(unittest.TestCase):
    def test_existing_divergence_coverage_cannot_be_dropped(self):
        case = {"category": "diagnostic", "minimum_coverage": ["color/tex:433"],
                "original_limitations": ["unobserved depth"]}
        self.assertTrue(gfx_revision._passes(
            case, {"verdict": "different", "coverage": ["color/tex:433"]}))
        self.assertTrue(gfx_revision._passes(
            case, {"verdict": "exact", "coverage": ["color/tex:433"]}))
        for verdict in ("unsupported", "execution_failure", "missing_original_observation"):
            with self.subTest(verdict=verdict):
                self.assertFalse(gfx_revision._passes(
                    case, {"verdict": verdict, "original_evidence_verified": True,
                           "problems": ["unobserved depth"]}))

    def test_only_verified_original_limitations_are_eligible(self):
        case = {"category": "diagnostic", "original_limitations": ["lossy depth"]}
        result = {"verdict": "missing_original_observation", "problems": ["lossy depth"]}
        self.assertFalse(gfx_revision._passes(case, result))
        self.assertTrue(gfx_revision._passes(
            case, dict(result, original_evidence_verified=True)))
        self.assertFalse(gfx_revision._passes(
            dict(case, category="exact"), dict(result, original_evidence_verified=True)))

    def test_mandatory_policy_rejects_lost_coverage_and_ignores_optional_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "candidate"
            workspace.mkdir()
            policy = {
                "trusted_revision": "trusted",
                "run_root": str(root / "runs"),
                "authentic_case": "original-draw",
                "cases": {
                    "original-draw": {"kind": "gl", "category": "exact"},
                    "metal-prefix": {"kind": "metal-prefix", "category": "diagnostic",
                                     "minimum_coverage": [0, 1]},
                    "optional-draw": {"kind": "gl", "category": "optional"},
                },
            }
            authentic = {"verdict": "exact", "execution": "completed",
                         "evidence_tier": "original-eu4"}
            incomplete = {"verdict": "different", "execution": "completed", "coverage": [0]}
            with (mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest")),
                  mock.patch.object(gfx_revision, "_git", return_value="tree"),
                  mock.patch.object(gfx_revision, "_gl_preflight", return_value=authentic) as gl,
                  mock.patch.object(gfx_revision, "_metal_prefix", return_value=incomplete)):
                report = gfx_revision.qualify(workspace, "revision")
                self.assertFalse(report["qualified"])
                self.assertEqual(report["required_cases"], ["original-draw", "metal-prefix"])
                self.assertEqual(report["executed_cases"], report["required_cases"])
                gl.assert_called_once()
                focused = gfx_revision.qualify(workspace, "revision", "original-draw")
                self.assertFalse(focused["qualified"])
                self.assertEqual(focused["executed_cases"], ["original-draw"])


    def test_reused_execution_keeps_its_revision_and_rechecks_its_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "observations.jsonl"
            output.write_text("{}")
            binding = {"candidate": {"executable_sha256": "abc"}, "reference": "case-a"}
            receipt, _ = dsh_receipts.write_receipt(root, {
                "schema": gfx_revision.SCHEMA, "execution": "completed",
                "execution_revision": "previous-commit", **binding,
                "outputs": [{"file": output.name, "bytes": 2, "sha256": sha256_file(output)}],
            })
            self.assertEqual(gfx_revision._applicable_execution(receipt, binding)[
                "execution_revision"], "previous-commit")
            self.assertIsNone(gfx_revision._applicable_execution(receipt, dict(binding, reference="b")))
            output.write_text("[]")
            self.assertIsNone(gfx_revision._applicable_execution(receipt, binding))


@unittest.skipUnless(sys.platform == "darwin" and os.getenv("EU4_QUALIFICATION_NATIVE") == "1",
                     "requires an offline native host that can apply Seatbelt")
class NativeQualificationTests(unittest.TestCase):
    def test_fence_and_candidate_function_corpus(self):
        with tempfile.TemporaryDirectory(prefix="qualification-") as scratch:
            root = Path(scratch).resolve()
            job = root / "worker"
            job.mkdir()
            allowed = root / "input"
            protected = root / "reference"
            allowed.write_text("authorized input")
            protected.write_text("expected output")
            observation = qualification_sandbox.run(
                ["/bin/cat", allowed], writable=job, readable=(allowed,))
            self.assertEqual(observation.returncode, 0, observation.stderr)
            self.assertEqual(observation.stdout, "authorized input")
            refusal = qualification_sandbox.run(["/bin/cat", protected], writable=job)
            self.assertNotEqual(refusal.returncode, 0)
            self.assertEqual(refusal.stdout, "")
            output = job / "observation"
            worker = qualification_sandbox.run(
                ["/bin/bash", "-c",
                 'printf actual > "$1" || exit 1; '
                 'if printf tampered > "$2"; then exit 2; fi',
                 "qualification-probe", output, protected], writable=job)
            self.assertEqual(worker.returncode, 0, worker.stderr)
            self.assertEqual(output.read_text(), "actual")
            self.assertEqual(protected.read_text(), "expected output")
            denied = qualification_sandbox.run(
                ["/bin/bash", "-c", "exec /usr/bin/true"], writable=job)
            self.assertNotEqual(denied.returncode, 0, denied.stderr)
            self.assertIn("Operation not permitted", denied.stderr)
            with self.assertRaises(subprocess.TimeoutExpired):
                qualification_sandbox.run(["/bin/sleep", "5"], writable=job, timeout=0.01)
            stage = root / "function"
            stage.mkdir()
            commit = gfx_revision._git("rev-parse", "HEAD")
            reference = {
                "corpus_sha256": reconstruction_diff.corpus_sha256(),
                "reference_sha256": sha256_file(reconstruction_diff.FUNCTION_DIR /
                                               "differential/reference.json"),
            }
            result = gfx_revision.qualify_function(commit, stage, reference)
            self.assertEqual(result["verdict"], "exact", result)
            self.assertEqual(result["scope"]["cases"], 414)

            populate = gfx_revision.candidate_snapshot.materialize_commit

            def corrupt_dirty_byte(repo, revision, destination, paths):
                digests = populate(repo, revision, destination, paths)
                source = destination / paths[0]
                body = source.read_text()
                correct = "ctx->dirty = kDirtyValue;"
                self.assertEqual(body.count(correct), 1)
                source.write_text(body.replace(correct, "ctx->dirty = 0;"))
                digests[paths[0]] = sha256_file(source)
                return digests

            mutated = root / "function-mutation"
            mutated.mkdir()
            with mock.patch.object(gfx_revision.candidate_snapshot, "materialize_commit",
                                   side_effect=corrupt_dirty_byte):
                result = gfx_revision.qualify_function(commit, mutated, reference)
            self.assertEqual(result["verdict"], "different", result.get("problems", [])[:1])
            self.assertTrue(result["problems"])

    def test_reviewed_gl_fixture_and_draw_omission_use_same_candidate(self):
        from automation import state

        primary = state.git_common_dir(gfx_revision.ROOT).parent
        fixture = primary / ".local/r2b-authority/07453ebc/review"
        if not fixture.is_dir():
            self.skipTest("retained R2b native fixture is unavailable")
        expectation = {
            "architecture": "arm64",
            "package_sha256": "704fce9fd9ccf031cd3761ca3ae50a1ab1f59ed7c3d1c79a69c0194e7cc11513",
            "expected_sha256": "e5f58358cc2305886bae06af04a2c55098b36d97e2e9eb01dbd9e4cef885b0a7",
            "gl": {"vendor": "Apple", "renderer": "Apple M5 Max", "version": "2.1 Metal - 91.7"},
            "observed_state": {
                "restore": {"mode": 4, "count": 6, "gl_error": 0, "linked": 1,
                            "scissor_enabled": 0, "viewport": [0, 0, 4, 4]},
                "post": {"submitted": 1, "gl_error": 0},
            },
        }
        with tempfile.TemporaryDirectory(prefix="qualification-gl-") as scratch:
            result = gfx_revision._selected_gl(
                gfx_revision._git("rev-parse", "HEAD"), Path(scratch),
                fixture / "package", fixture / "expected.bgra", expectation)
        self.assertEqual(result["verdict"], "exact", result)
        self.assertTrue(result["controls"]["draw"]["post"]["exact"])
        self.assertTrue(result["controls"]["omit"]["restore"]["exact"])
        self.assertFalse(result["controls"]["omit"]["post"]["exact"])

        stage_candidate = gfx_revision.candidate_snapshot.materialize_commit

        def force_scissor(repo, revision, destination, paths):
            digests = stage_candidate(repo, revision, destination, paths)
            candidate = destination / paths[0]
            code = candidate.read_text()
            self.assertEqual(code.count("if (g.scissor_test) {"), 1)
            candidate.write_text(code.replace("if (g.scissor_test) {", "if (1) {"))
            digests[paths[0]] = sha256_file(candidate)
            return digests

        with tempfile.TemporaryDirectory(prefix="qualification-state-") as scratch:
            with mock.patch.object(gfx_revision.candidate_snapshot, "materialize_commit",
                                   side_effect=force_scissor):
                result = gfx_revision._selected_gl(
                    gfx_revision._git("rev-parse", "HEAD"), Path(scratch),
                    fixture / "package", fixture / "expected.bgra", expectation)
        self.assertEqual(result["verdict"], "different", result)
        self.assertTrue(result["controls"]["draw"]["post"]["exact"])
        self.assertEqual(result["earliest_observed_divergence"]["phase"], "restore")
        self.assertEqual(result["earliest_observed_divergence"]["field"], "scissor_enabled")


    def test_fenced_m7_prefix_protects_event_zero_and_observes_event_one(self):
        if not (evidence_root := os.environ.get("EU4_QUALIFICATION_M7_EVIDENCE_ROOT")):
            self.skipTest("published R3 M7 reference root is unconfigured")
        evidence = Path(evidence_root)
        key = "47590918-d63a-4798-9737-df00285ad51b-1-8dd94e482d169e24-reference"
        if not (evidence / f"locators/{key}.json").is_file():
            self.skipTest("published R3 M7 reference is unavailable")
        candidate = gfx_revision._git("rev-parse", "HEAD")
        case = {
            "category": "diagnostic", "require_end": 1, "preserve_exact": [0],
            "minimum_coverage": [0, 1],
            "reference": {
                "key": key,
                "seal_sha256": "8dd94e482d169e247e55a584309ac982de82dc01cf548c8ce857a6516b58654f",
                "manifest_sha256": "c9d0993fa9e5f2b2d9eed16f258c94ddb71f558d76c5ac1077b155ce653efc05",
            },
        }
        with tempfile.TemporaryDirectory(prefix="qualification-metal-") as scratch:
            result = gfx_revision._metal_prefix(
                {"trusted_revision": candidate, "evidence_roots": [str(evidence)]},
                case, gfx_revision.ROOT, candidate, Path(scratch))
        self.assertTrue(gfx_revision._passes(case, result), result)
        self.assertIn(0, result["preserved_exact"])
        self.assertIn(1, result["coverage"])
