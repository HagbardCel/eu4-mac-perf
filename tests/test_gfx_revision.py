from pathlib import Path
import json
import os
import socket
import sys
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from automation import (candidate_snapshot, case_locator, dsh_receipts, gfx_ed25519,
                        gfx_gl_control_qualification, gfx_prefix_check, gfx_revision,
                        qualification_sandbox, state)
from automation.gfx_artifacts import sha256_bytes, sha256_file
from reverse_engineering import reconstruction_diff


class ProtectedDiagnosticTests(unittest.TestCase):
    def test_existing_divergence_coverage_cannot_be_dropped(self):
        case = {"category": "diagnostic", "minimum_coverage": ["color/tex:433"],
                "original_limitations": ["unobserved depth"]}
        self.assertTrue(dsh_receipts.case_satisfies(
            case, {"verdict": "different", "coverage": ["color/tex:433"],
                   "execution": "completed", "independently_verified": True}))
        self.assertTrue(dsh_receipts.case_satisfies(
            case, {"verdict": "exact", "coverage": ["color/tex:433"],
                   "execution": "completed", "independently_verified": True}))
        for verdict in ("unsupported", "execution_failure", "missing_original_observation"):
            with self.subTest(verdict=verdict):
                self.assertFalse(dsh_receipts.case_satisfies(
                    case, {"verdict": verdict, "original_evidence_verified": True,
                           "problems": ["unobserved depth"]}))

    def test_only_verified_original_limitations_are_eligible(self):
        case = {"category": "diagnostic", "original_limitations": ["lossy depth"]}
        result = {"verdict": "missing_original_observation", "problems": ["lossy depth"]}
        self.assertFalse(dsh_receipts.case_satisfies(case, result))
        self.assertTrue(dsh_receipts.case_satisfies(
            case, dict(result, original_evidence_verified=True)))
        self.assertFalse(dsh_receipts.case_satisfies(
            dict(case, category="exact"), dict(result, original_evidence_verified=True)))

    def test_mandatory_policy_rejects_lost_coverage_and_ignores_optional_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "candidate"
            workspace.mkdir()
            seed = bytes(range(32))
            key_file = root / "key"
            key_file.write_text(seed.hex())
            key_file.chmod(0o600)
            policy = {
                "receipt_signing_key": str(key_file),
                "receipt_public_key": gfx_ed25519.derive_public_key(seed).hex(),
                "trusted_revision": "trusted",
                "run_root": str(root / "runs"),
            }
            contract = root / "contract.json"
            contract.write_text(json.dumps({
                "schema": gfx_revision.POLICY_SCHEMA, "authentic_case": "original-draw",
                "cases": {
                    "original-draw": {"kind": "gl", "category": "exact"},
                    "metal-prefix": {"kind": "metal-prefix", "category": "diagnostic",
                                     "minimum_coverage": [0, 1]},
                },
            }))
            authentic = {"verdict": "exact", "execution": "completed",
                         "evidence_tier": "original-eu4", "independently_verified": True}
            incomplete = {"verdict": "different", "execution": "completed", "coverage": [0]}

            def execute(_root, _commit, job, _source, _case):
                (job / "draw").mkdir()
                (job / "draw" / "attachment").write_bytes(b"pixels")
                return dict(authentic, outputs=dsh_receipts.artifact_records(job, ["draw/attachment"]))

            with (mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest")),
                  mock.patch.object(gfx_revision, "POLICY_CONTRACT", contract),
                  mock.patch.object(state, "trusted_git", return_value="tree"),
                  mock.patch.object(gfx_gl_control_qualification, "qualify_published_candidate", side_effect=execute) as gl,
                  mock.patch.object(case_locator, "resolve_sealed_reference", return_value=((None, None, None, {}), None)),
                  mock.patch.object(gfx_prefix_check, "qualify_candidate", return_value=incomplete)):
                report = gfx_revision.qualify(workspace, "revision")
                self.assertFalse(report["qualified"])
                self.assertEqual(report["required_cases"], ["original-draw", "metal-prefix"])
                self.assertEqual(report["executed_cases"], report["required_cases"])
                gl.assert_called_once()
                focused = gfx_revision.qualify(workspace, "revision", "original-draw")
                self.assertFalse(focused["qualified"])
                self.assertEqual(focused["executed_cases"], ["original-draw"])

                path = Path(report["receipt"])
                kwargs = {
                    "revision": "revision", "policy_sha256": "digest",
                    "contract_path": contract, "run_root": root / "runs",
                }
                key = gfx_ed25519.derive_public_key(seed)
                verified = dsh_receipts.verify_revision(path, key, **kwargs)
                self.assertFalse(verified["qualified"])
                dsh_receipts.write_receipt(path.parent, dict(verified, qualified=True), seed=seed)
                with self.assertRaisesRegex(ValueError, "aggregate verdict"):
                    dsh_receipts.verify_revision(path, key, **kwargs)
                dsh_receipts.write_receipt(path.parent, verified, seed=seed)
                receipt = Path(report["cases"][0]["receipt"])
                original = dsh_receipts.read_json(receipt)
                output = receipt.parent / "draw" / "attachment"
                output.write_bytes(b"spoofs")
                with self.assertRaisesRegex(ValueError, "case output changed"):
                    dsh_receipts.verify_revision(path, key, **kwargs)
                output.write_bytes(b"pixels")
                dsh_receipts.write_receipt(receipt.parent, dict(original, verdict="different"), seed=seed)
                with self.assertRaisesRegex(ValueError, "substituted case receipt"):
                    dsh_receipts.verify_revision(path, key, **kwargs)

                policy["cases"] = {}
                with self.assertRaisesRegex(gfx_revision.QualificationError, "cannot change"):
                    gfx_revision.qualify(workspace, "revision")


    def test_protected_raw_pair_binds_scope_inputs_and_comparison_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "archive" / "pair"
            archive.mkdir(parents=True)
            reference = b"\x00" * 16
            candidate = b"\x10" + reference[1:]
            (archive / "ref.bgra").write_bytes(reference)
            image = archive / "candidate.bgra"
            image.write_bytes(candidate)
            scope = {"width": 2, "height": 2, "origin": "lower_left", "pixel_format": "BGRA8"}
            comparison = {"status": "fail", "pixel_verdict": "different",
                          "mismatched_pixels": 1, "max_channel_error": 16}
            rule = {
                "kind": "raw-pair", "category": "diagnostic", "evidence_tier": "retained-original-pair",
                "minimum_coverage": ["raw-pair-comparison"],
                "required_scope": {**scope, "mismatched_pixels": 1, "max_channel_error": 16},
                "reference": {"archive_path": "pair", "manifest_sha256": "a" * 64,
                              "complete_manifest_unavailable": True,
                              "reference_sha256": sha256_bytes(reference),
                              "candidate_sha256": sha256_bytes(candidate)},
                "configuration": {
                    "raw_scope": scope, "frames": {"reference": "ref.bgra", "candidate": "candidate.bgra"},
                    "comparison_policy": {"policy": "tolerant", "max_channel_error": 8,
                                          "regions": {"top": {"rect": [0, 1, 0, 2]}}},
                    "expected_comparison": {**comparison, "regions": {
                        "top": {"mismatched_pixels": 0, "max_channel_error": 0}}},
                },
            }
            document = {
                "schema": gfx_revision.POLICY_SCHEMA, "authentic_case": "original-draw",
                "cases": {"original-draw": {"kind": "gl", "category": "exact"}, "pair": rule},
            }
            contract = root / "contract.json"
            contract.write_text(json.dumps(document))
            workspace = root / "candidate"
            workspace.mkdir()
            seed = bytes(range(32))
            key_file = root / "key"
            key_file.write_text(seed.hex())
            key_file.chmod(0o600)
            key = gfx_ed25519.derive_public_key(seed)
            policy = {
                "trusted_revision": "trusted", "run_root": str(root / "runs"),
                "evidence_roots": [str(root / "archive")],
                "receipt_signing_key": str(key_file), "receipt_public_key": key.hex(),
            }
            kwargs = {"revision": "revision", "policy_sha256": "digest",
                      "contract_path": contract, "run_root": root / "runs"}
            with (mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest")),
                  mock.patch.object(gfx_revision, "POLICY_CONTRACT", contract),
                  mock.patch.object(state, "trusted_git", return_value="tree"),
                  mock.patch.object(case_locator, "resolve_sealed_reference", return_value=(
                      None, {"verdict": "missing_original_observation", "problems": ["capture unavailable"]}))):
                aggregate = gfx_revision.qualify(workspace, "revision")
                receipt = Path(aggregate["receipt"])
                row = aggregate["cases"][1]
                self.assertTrue(row["requirement_satisfied"])
                self.assertEqual(row["evidence_locators"]["candidate"], "pair/candidate.bgra")
                self.assertTrue(row["reference"]["complete_manifest_unavailable"])
                artifact = Path(row["receipt"]).parent / row["outputs"][0]["file"]
                self.assertEqual(json.loads(artifact.read_text())["comparison"]["statistics"]["tolerance"], 8)
                self.assertFalse(dsh_receipts.verify_revision(receipt, key, **kwargs)["qualified"])

                image.write_bytes(reference)
                self.assertEqual(gfx_revision.qualify(workspace, "revision", "pair")["cases"][0]["verdict"],
                                 "invalid_provenance")
                # The receipt attests an earlier read. It does not recover or reverify the source bytes.
                dsh_receipts.verify_revision(receipt, key, **kwargs)
                image.write_bytes(candidate)
                artifact.write_bytes(b"altered comparison")
                with self.assertRaisesRegex(ValueError, "case output changed: pair"):
                    dsh_receipts.verify_revision(receipt, key, **kwargs)

                scope["origin"] = "top_left"
                contract.write_text(json.dumps(document))
                self.assertEqual(gfx_revision.qualify(workspace, "revision", "pair")["cases"][0]["verdict"],
                                 "regression")

    def test_required_evidence_root_and_optional_archive_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "candidate"
            workspace.mkdir()
            missing = root / "archive"
            resolved = root / "primary" / "m7" / "case"
            resolved.mkdir(parents=True)
            roots = [{"path": str(missing), "required": False},
                     {"path": str(root / "primary"), "required": True}]
            reference = {"archive_path": "m7/case", "key": "retained"}
            self.assertEqual(case_locator.resolve_legacy_reference(roots, reference, workspace),
                             resolved.resolve())
            roots[0]["required"] = True
            with self.assertRaisesRegex(case_locator.CaseLocatorError, "missing_required_evidence_root"):
                case_locator.resolve_legacy_reference(roots, reference, workspace)
            source, refusal = case_locator.resolve_sealed_reference(roots, reference, workspace)
            self.assertIsNone(source)
            self.assertEqual(refusal["verdict"], "missing_required_evidence_root")

    def test_reused_execution_keeps_its_revision_and_rechecks_its_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "observations.jsonl"
            output.write_text("{}")
            binding = {"candidate": {"executable_sha256": "abc"}, "reference": "case-a"}
            seed = bytes(range(32))
            public_key = gfx_ed25519.derive_public_key(seed)
            receipt, original = dsh_receipts.write_receipt(root, {
                "schema": gfx_revision.SCHEMA, "execution": "completed",
                "execution_revision": "previous-commit", **binding,
                "outputs": [{"file": output.name, "bytes": 2, "sha256": sha256_file(output)}],
            }, seed=seed)
            self.assertEqual(dsh_receipts.applicable_execution(receipt, binding, public_key)[
                "execution_revision"], "previous-commit")
            self.assertIsNone(dsh_receipts.applicable_execution(receipt, dict(binding, reference="b"), public_key))
            dsh_receipts.write_receipt(root, dict(original, verdict="exact"), seed=bytes(32))
            self.assertIsNone(dsh_receipts.applicable_execution(receipt, binding, public_key))
            dsh_receipts.write_receipt(root, original, seed=seed)
            output.write_text("[]")
            self.assertIsNone(dsh_receipts.applicable_execution(receipt, binding, public_key))


@unittest.skipUnless(sys.platform == "darwin" and os.getenv("EU4_QUALIFICATION_NATIVE") == "1",
                     "requires an offline native host that can apply Seatbelt")
class NativeQualificationTests(unittest.TestCase):
    def test_worker_profiles_protect_files_network_and_descendants(self):
        from automation import native_build

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            worker = root / "worker"
            worker.mkdir()
            protected = [root / name for name in ("reference", "policy", "validator",
                                                  "git-metadata", "credential", "receipt")]
            for item in protected:
                item.write_bytes(b"protected")
            game = root / "eu4"
            game.write_text("#!/bin/sh\nexit 0\n")
            game.chmod(0o755)
            code = root / "probe.c"
            code.write_text(r"""
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
int main(int argc, char **argv) {
    for (int i = 3; i < argc; i++) {
        if (open(argv[i], O_RDONLY) >= 0 || errno != EPERM) return 2;
        if (open(argv[i], O_WRONLY | O_TRUNC) >= 0 || errno != EPERM) return 3;
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0 && errno != EPERM && errno != EACCES) return 4;
    if (fd >= 0) {
        struct sockaddr_in remote = {.sin_family = AF_INET, .sin_port = htons(atoi(argv[1])),
                                     .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
        if (connect(fd, (struct sockaddr *)&remote, sizeof(remote)) == 0
            || (errno != EPERM && errno != EACCES)) return 5;
        close(fd);
    }
    execl(argv[2], argv[2], (char *)0);
    return errno == EPERM || errno == EACCES ? 0 : 6;
}
""")
            compiler, sdk = native_build.qualification_toolchain("clang")
            binary = root / "probe"
            subprocess.run([compiler, "-isysroot", sdk, code, "-o", binary],
                           check=True, timeout=30)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                arguments = [binary, listener.getsockname()[1], game, *protected]
                build = qualification_sandbox.run(
                    ["/bin/bash", "-c", '"$@" && exit 0', "build-probe", *arguments],
                    writable=worker, tools=(binary,))
                self.assertEqual(build.returncode, 0, build.stderr)
                for profile in ("gl", "metal"):
                    with self.subTest(profile=profile):
                        probe = qualification_sandbox.run(arguments, writable=worker, graphics=profile)
                        self.assertEqual(probe.returncode, 0, probe.stderr)
                translated = root / "probe-x86_64"
                subprocess.run([compiler, "-arch", "x86_64", "-isysroot", sdk, code,
                                "-o", translated], check=True, timeout=30)
                probe = qualification_sandbox.run(
                    [translated, *arguments[1:]], writable=worker, graphics="gl", translated=True)
                self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertTrue(all(path.read_bytes() == b"protected" for path in protected))

            pid_file = worker / "descendant"
            with self.assertRaises(subprocess.TimeoutExpired):
                qualification_sandbox.run(
                    ["/bin/bash", "-c", '/bin/sleep 30 & printf "%s" "$!" > "$1"; wait',
                     "build-probe", pid_file], writable=worker, tools=("/bin/sleep",), timeout=1)
            pid = int(pid_file.read_text())
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("build descendant survived the worker deadline")

    @staticmethod
    def _native_capture(repo: Path, stage: Path, frame: Path) -> tuple[dict, ...]:
        from automation import native_build

        compiler, sdk = native_build.qualification_toolchain("clang")
        host = stage / "capture-host"
        interposer = stage / "capture-interposer.dylib"
        subprocess.run([
            compiler, "-arch", "x86_64", "-isysroot", sdk, "-O1", "-dynamiclib",
            "-undefined", "dynamic_lookup", "-Wno-deprecated-declarations",
            "-o", interposer, repo / "benchmark/eu4_gfx_gl_interpose.c",
            "-framework", "OpenGL",
        ], check=True, timeout=90)
        subprocess.run([
            compiler, "-arch", "x86_64", "-isysroot", sdk, "-O1", "-fobjc-arc",
            "-Wl,-export_dynamic,-no_fixup_chains", "-DGL_SILENCE_DEPRECATION", "-Wno-deprecated-declarations",
            "-I", repo / "benchmark", "-o", host, repo / "tests/gfx_trace_state_host.m",
            repo / "benchmark/eu4_gfx_frame_trace.c", repo / "benchmark/eu4_gfx_frame_observe.c",
            "-framework", "OpenGL", "-framework", "AppKit",
        ], check=True, timeout=90)
        fixups = subprocess.check_output(
            ["/usr/bin/xcrun", "dyld_info", "-fixups", host], text=True, timeout=30)
        if not any("lazy-bind" in row and "OpenGL/_glDrawArrays" in row
                   for row in fixups.splitlines()):
            raise AssertionError("native dispatch fixture lacks the original lazy OpenGL import")
        return tuple(json.loads(line) for line in subprocess.check_output(
            [host, frame, "selected"], text=True, timeout=30,
            env={**os.environ, "DYLD_INSERT_LIBRARIES": str(interposer)}).splitlines())

    @staticmethod
    def _seal_native_fixture(frame: Path, draw: dict, post: dict, gl: dict):
        from automation import gfx_frame

        stream = frame / "frame-events.jsonl"
        stream.write_text(json.dumps(draw) + "\n")
        observations = frame / "prefix-observations.jsonl"
        observations.write_text(json.dumps(post) + "\n")
        reference = frame / "frame-reference.bgra"
        reference.write_bytes(bytes(4))
        manifest = frame / "frame-trace.json"
        manifest.write_text(json.dumps({
            "schema": gfx_frame.TRACE_SCHEMA, "complete": True, "events": 1, "test_only": True,
            "process_architecture": "x86_64", "gl": gl,
            "frame": {"reference": reference.name, "reference_bytes": 4,
                      "reference_sha256": sha256_file(reference)},
            "prefix": {"schema": "gfx-observations/v1", "mode": "selected",
                       "status": "complete", "authority": "synthetic", "events": [0],
                       "count": 1, "post_event_count": 1,
                       "observation_file": observations.name,
                       "observation_sha256": sha256_file(observations),
                       "observation_bytes": observations.stat().st_size},
        }))
        (frame / "frame-seal.json").write_text(json.dumps({
            "schema": gfx_frame.SEAL_SCHEMA, "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": gfx_frame.SEAL_ABI_VERSION,
            "capture_frame_id": 1, "session_id": "synthetic-rgba8", "pid": 1234,
            "operation_count": 1, "draw_count": 1, "clear_count": 0, "copy_count": 0,
            "capture_dir": str(frame), "width": 1, "height": 1, "pixel_format": "BGRA8",
            "trace_complete": True, "trace_integrity": True, "readback_ok": True,
            "event_stream_file": stream.name, "event_stream_bytes": stream.stat().st_size,
            "event_stream_sha256": sha256_file(stream), "event_stream_verified": True,
            "manifest_file": manifest.name, "manifest_sha256": sha256_file(manifest),
            "reference_file": reference.name, "reference_bytes": 4,
            "reference_sha256": sha256_file(reference), "test_only": True,
        }))
        trace = gfx_frame.load_trace(frame)
        return frame, trace, gfx_frame.load_seal(frame, trace=trace), {}

    def test_offscreen_shape_runs_with_native_observations_and_sampled_texture(self):
        repo = gfx_revision.ROOT
        with tempfile.TemporaryDirectory() as scratch:
            stage = Path(scratch)
            frame = stage / "frame"
            pre, native_draw, post, gl = self._native_capture(repo, stage, frame)
            self.assertEqual(native_draw.pop("fixture_samples"), 864 * 558)
            self.assertFalse(native_draw.pop("fixture_capture_failed"))
            failed_event = native_draw.pop("failed_event")
            self.assertTrue(failed_event["fixture_capture_failed"])
            failure = failed_event["submission_failure"]
            self.assertEqual(failure["reason"], "submission_arguments")
            self.assertEqual(failure["expected_arguments"], [5, 0, 4])
            self.assertEqual(failure["actual_arguments"], [5, 0, 0])
            self.assertEqual(failure["count"], 1)
            self.assertIsNone(failure["submission_target"]["gl_error"])
            missing = native_draw.pop("missing_event")["submission_failure"]
            self.assertEqual((missing["reason"], missing["count"]), ("missing_submission", 0))
            error = native_draw.pop("error_event")["submission_failure"]
            self.assertEqual(error["reason"], "entry_target")
            self.assertEqual(error["entry_target"]["gl_error"], 0x500)
            changed = native_draw.pop("target_event")["submission_failure"]
            self.assertEqual(changed["reason"], "exit_target")
            self.assertEqual(changed["count"], 1)
            self.assertEqual(changed["entry_target"]["read_framebuffer"],
                             changed["submission_target"]["read_framebuffer"])
            self.assertGreater(changed["entry_target"]["read_framebuffer"], 0)
            self.assertEqual(changed["exit_target"]["read_framebuffer"], 0)
            self.assertIsNone(changed["exit_target"]["gl_error"])
            self.assertIsNone(changed["exit_target"]["attachment"])
            self.assertIsNone(changed["exit_target"]["depth_type"])
            draw = {"kind": "draw", "index": 0, **pre, **native_draw}
            shader = draw["program"]["shaders"]
            source = self._seal_native_fixture(frame, draw, post, gl)
            selection = {"workload": {
                "caller_rva": "0x1", "shader_sha256": {row["stage"]: row["sha256"] for row in shader},
                "color_attachment": {key: post["target"]["color_attachment"][key]
                                     for key in ("type", "level", "internal_format", "width", "height")},
            }}
            case = {"selection": selection, "reference": {"key": "native-synthetic-fixture"}}
            head = state.trusted_git(repo, "rev-parse", "HEAD").strip()
            result = gfx_gl_control_qualification.qualify_published_candidate(
                repo, head, stage, source, case)
            self.assertEqual(result["verdict"], "exact", {
                key: result.get(key) for key in ("problems", "earliest_observed_divergence")})
            self.assertEqual(result["evidence_tier"], "synthetic")
            self.assertFalse(result["controls"]["omit"]["post"]["exact"])
            expected = stage / "trusted-post.bgra"
            restricted = Path(stage / "inputs")
            package_sha = json.loads((restricted / "replay-input.json").read_text())["package_sha256"]
            self.assertNotIn(sha256_file(expected), {sha256_file(path)
                             for path in (restricted / "blobs").iterdir()})
            incomplete = dict(draw)
            del incomplete["submission"]
            refused = self._seal_native_fixture(frame, incomplete, post, gl)
            checked = gfx_gl_control_qualification.qualify_published_candidate(
                repo, head, stage / "refused", refused, case)
            self.assertEqual(checked["verdict"], "missing_original_observation", checked)
            self.assertEqual(checked["execution"], "not_started")
            wrong_driver = self._seal_native_fixture(frame, draw, post, dict(gl, vendor="Different vendor"))
            wrong_driver_stage = stage / "wrong-driver"
            wrong_driver_stage.mkdir()
            rejected_driver = gfx_gl_control_qualification.qualify_published_candidate(
                repo, head, wrong_driver_stage, wrong_driver, case)
            self.assertEqual(rejected_driver["verdict"], "candidate_environment_mismatch")

            populate = candidate_snapshot.materialize_commit

            def two_draws(repo, revision, destination, files):
                digests = populate(repo, revision, destination, files)
                source = destination / files[0]
                body = source.read_text()
                submission = "        glDrawArrays((GLenum)g.mode, g.first, g.count);"
                self.assertEqual(body.count(submission), 1)
                steps = "\n".join(("        glScissor(0, 0, g.width, g.height);",
                                   "        glEnable(GL_SCISSOR_TEST);", submission,
                                   "        glDisable(GL_SCISSOR_TEST);", submission))
                source.write_text(body.replace(submission, steps))
                digests[files[0]] = sha256_file(source)
                return digests

            sequence = stage / "sequence"
            sequence.mkdir()
            with mock.patch.object(candidate_snapshot, "materialize_commit", side_effect=two_draws):
                result = gfx_gl_control_qualification.qualify_candidate(
                    repo, state.trusted_git(repo, "rev-parse", "HEAD").strip(), sequence,
                    restricted, expected,
                    {"architecture": "x86_64", "package_sha256": package_sha,
                     "expected_sha256": sha256_file(expected), "gl": {}, "repeat_count": 2})
            self.assertEqual(result["verdict"], "different", result.get("problems"))
            self.assertEqual(result["scope"]["transitions"], 2)
            self.assertEqual(result["earliest_observed_divergence"], {
                "transition": 0, "phase": "restore", "field": "scissor_enabled",
                "expected": 0, "actual": 1})
            self.assertTrue(result["controls"]["draw"]["steps"][1]["restore"]["exact"])
            self.assertTrue(result["controls"]["draw"]["post"]["exact"])

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
            commit = state.trusted_git(gfx_revision.ROOT, "rev-parse", "HEAD").strip()
            signing_key = root / "key"
            seed = bytes(range(32))
            signing_key.write_text(seed.hex())
            signing_key.chmod(0o600)
            policy = {"run_root": str(root / "runs"),
                      "trusted_revision": commit,
                      "receipt_signing_key": str(signing_key),
                      "receipt_public_key": gfx_ed25519.derive_public_key(seed).hex()}
            workspace = root / "candidate"
            workspace.mkdir()
            with mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest")):
                first = gfx_revision.qualify(workspace, commit, "constant-buffer")["cases"][0]
                self.assertEqual(first["verdict"], "exact", first)
                self.assertEqual(first["scope"]["cases"], 414)
                relative = Path(first["receipt"]).relative_to(root / "runs").as_posix()
                policy["evidence"] = {"constant-buffer": {"reuse_receipt": relative}}
                reused = gfx_revision.qualify(workspace, commit, "constant-buffer")["cases"][0]
                self.assertTrue(reused.get("execution_reused"), reused)
                self.assertEqual(reused["receipt"], first["receipt"])
                self.assertEqual(reused["execution_revision"], commit)

            populate = candidate_snapshot.materialize_commit

            def corrupt_dirty_byte(repo, revision, destination, paths):
                digests = populate(repo, revision, destination, paths)
                source = destination / paths[0]
                body = source.read_text()
                correct = "ctx->dirty = kDirtyValue;"
                self.assertEqual(body.count(correct), 1)
                source.write_text(body.replace(correct, "ctx->dirty = 0;"))
                digests[paths[0]] = sha256_file(source)
                return digests

            with (mock.patch.object(candidate_snapshot, "materialize_commit",
                                    side_effect=corrupt_dirty_byte),
                  mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest"))):
                result = gfx_revision.qualify(workspace, commit, "constant-buffer")["cases"][0]
            self.assertFalse(result.get("execution_reused"))
            self.assertNotEqual(result["receipt"], first["receipt"])
            self.assertEqual(result["verdict"], "different", result.get("problems", [])[:1])
            self.assertTrue(result["problems"])

    def test_reviewed_gl_fixture_and_draw_omission_use_same_candidate(self):
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
            result = gfx_gl_control_qualification.qualify_candidate(
                gfx_revision.ROOT,                 state.trusted_git(gfx_revision.ROOT, "rev-parse", "HEAD").strip(), Path(scratch),
                fixture / "package", fixture / "expected.bgra", expectation)
        self.assertEqual(result["verdict"], "exact", result)
        self.assertTrue(result["controls"]["draw"]["post"]["exact"])
        self.assertTrue(result["controls"]["omit"]["restore"]["exact"])
        self.assertFalse(result["controls"]["omit"]["post"]["exact"])

        stage_candidate = candidate_snapshot.materialize_commit

        def force_scissor(repo, revision, destination, paths):
            digests = stage_candidate(repo, revision, destination, paths)
            candidate = destination / paths[0]
            code = candidate.read_text()
            self.assertEqual(code.count("if (g.scissor_test) {"), 1)
            candidate.write_text(code.replace("if (g.scissor_test) {", "if (1) {"))
            digests[paths[0]] = sha256_file(candidate)
            return digests

        with tempfile.TemporaryDirectory(prefix="qualification-state-") as scratch:
            with mock.patch.object(candidate_snapshot, "materialize_commit",
                                   side_effect=force_scissor):
                result = gfx_gl_control_qualification.qualify_candidate(
                gfx_revision.ROOT,                     state.trusted_git(gfx_revision.ROOT, "rev-parse", "HEAD").strip(), Path(scratch),
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
        candidate = state.trusted_git(gfx_revision.ROOT, "rev-parse", "HEAD").strip()
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
            source, refusal = case_locator.resolve_sealed_reference([str(evidence)], case["reference"], gfx_revision.ROOT)
            self.assertIsNone(refusal)
            frame, _, _, trusted = source
            result = gfx_prefix_check.qualify_candidate(
                gfx_revision.ROOT, candidate, candidate, Path(scratch), frame, trusted, case)
        self.assertTrue(dsh_receipts.case_satisfies(case, result), result)
        self.assertIn(0, result["preserved_exact"])
        self.assertIn(1, result["coverage"])
