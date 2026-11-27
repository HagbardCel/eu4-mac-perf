from pathlib import Path
import copy
import json
import os
import socket
import struct
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
        self.assertTrue(gfx_revision._passes(
            case, {"verdict": "different", "coverage": ["color/tex:433"],
                   "execution": "completed", "independently_verified": True}))
        self.assertTrue(gfx_revision._passes(
            case, {"verdict": "exact", "coverage": ["color/tex:433"],
                   "execution": "completed", "independently_verified": True}))
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
            seed = bytes(range(32))
            key_file = root / "key"
            key_file.write_text(seed.hex())
            key_file.chmod(0o600)
            policy = {
                "receipt_signing_key": str(key_file),
                "receipt_public_key": gfx_ed25519.derive_public_key(seed).hex(),
                "trusted_revision": "trusted",
                "run_root": str(root / "runs"),
                "authentic_case": "original-draw",
                "cases": {
                    "original-draw": {"kind": "gl", "category": "exact", "reference": {}},
                    "metal-prefix": {"kind": "metal-prefix", "category": "diagnostic",
                                     "minimum_coverage": [0, 1], "reference": {}},
                    "optional-draw": {"kind": "gl", "category": "optional"},
                },
            }
            authentic = {"verdict": "exact", "execution": "completed",
                         "evidence_tier": "original-eu4", "independently_verified": True}
            incomplete = {"verdict": "different", "execution": "completed", "coverage": [0]}
            with (mock.patch.object(gfx_revision, "_policy", return_value=(policy, "digest")),
                  mock.patch.object(state, "trusted_git", return_value="tree"),
                  mock.patch.object(gfx_gl_control_qualification, "qualify_published_candidate", return_value=authentic) as gl,
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

    def test_offscreen_shape_runs_with_verified_clear_and_sampled_texture(self):
        repo = gfx_revision.ROOT
        fixture = state.git_common_dir(repo).parent / ".local/r2b-authority/07453ebc/review"
        if not fixture.is_dir():
            self.skipTest("retained GL state fixture is unavailable")
        package = json.loads((fixture / "package" / "replay-input.json").read_text())
        with tempfile.TemporaryDirectory() as scratch:
            stage = Path(scratch)
            frame = stage / "frame"
            blobs = frame / "blobs"
            blobs.mkdir(parents=True)

            def blob(name, value):
                (blobs / name).write_bytes(value)
                return {"file": name, "sha256": sha256_bytes(value), "bytes": len(value)}

            w, h = 864, 558
            tw, th = 1728, 1117
            sample = bytes((61, 97, 163, 255))
            expected = blob("expected", bytes((163, 97, 61, 255)) * (w * h))
            tex = blob("sample", sample * (tw * th))
            vertex = blob("vertices", struct.pack("<16h", -1, -1, 0, 1, 1, -1, 0, 1,
                                                   -1, 1, 0, 1, 1, 1, 0, 1))
            shader = [
                {"stage": "vertex", **blob("vertex", b"#version 120\nattribute vec4 in_POSITION;"
                  b"uniform vec4 UNIFORM1[9]; varying vec2 uv;"
                  b"void main(){ gl_Position=in_POSITION+UNIFORM1[8]; uv=in_POSITION.xy*.5+.5; }")},
                {"stage": "pixel", **blob("pixel", b"#version 120\nvarying vec2 uv;"
                  b"uniform sampler2D tex0; void main(){ gl_FragColor=texture2D(tex0,uv); }")},
            ]
            tint = blob("uniform", bytes(9 * 16))
            state_record = copy.deepcopy(package["state"])
            state_record["viewport"] = [0, 0, w, h]
            state_record["scissor"] = {"enabled": False, "box": [0, 0, w, h]}
            draw = {
                "kind": "draw", "index": 0, "state": state_record,
                "draw": {"count": 4, "mode": 5, "first": 0, "indexed": False, "instance_count": 1},
                "target": {"color_attachment": {"width": w, "height": h}, "framebuffer": 9},
                "program": {"name": 1, "shaders": shader,
                            "uniforms": [{"name": "tex0", "type": "sampler", "unit": 0},
                                         {"name": "UNIFORM1[0]", "type": "float",
                                          "components": 4, "elements": 9, **tint}]},
                "attributes": [{**package["attributes"][0], "data": vertex["file"],
                                "sha256": vertex["sha256"], "components": 4,
                                "gl_type": "0x1402", "range_bytes": 32}],
                "textures": [{"target": "2D", "name": 7, "sampler_name": "tex0", "unit": 0,
                              "width": tw, "height": th, "internal_format": "RGBA8",
                              "levels": [{"level": 0, "width": tw, "height": th,
                                          "internal_format": "RGBA8", "pixel_format": "RGBA8",
                                          "pixel_type": "unsigned_byte", **tex}],
                              "sampler": {"min": "0x2600", "mag": "0x2600", "wrap_s": "0x812f",
                                          "wrap_t": "0x812f", "base_level": 0, "max_level": 1000,
                                          "compare_mode": "0x0", "compare_func": "0x203",
                                          "min_lod": -1000, "max_lod": 1000, "lod_bias": 0,
                                          "anisotropy": 1}}],
            }
            trace = {"_events": [draw]}
            facts = {"scope": {"draw": 0, "resource": "tex:8"},
                     "initial_color": {"bgra": [0, 0, 0, 0]}, "post_color": expected}
            restricted = gfx_gl_control_qualification._offscreen_package(frame, trace, {}, facts, stage)
            result = gfx_gl_control_qualification.qualify_candidate(
                repo, state.trusted_git(repo, "rev-parse", "HEAD").strip(), stage,
                Path(restricted["path"]), blobs / "expected",
                {"architecture": "arm64", "package_sha256": restricted["package_sha256"],
                 "expected_sha256": expected["sha256"], "gl": {}})
            self.assertEqual(result["verdict"], "exact", {
                key: result.get(key) for key in ("problems", "earliest_observed_divergence")})
            self.assertEqual(result["evidence_tier"], "synthetic")
            self.assertFalse(result["controls"]["omit"]["post"]["exact"])

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
                    Path(restricted["path"]), blobs / "expected",
                    {"architecture": "arm64", "package_sha256": restricted["package_sha256"],
                     "expected_sha256": expected["sha256"], "gl": {}, "repeat_count": 2})
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
            stage = root / "function"
            stage.mkdir()
            commit = state.trusted_git(gfx_revision.ROOT, "rev-parse", "HEAD").strip()
            reference = {
                "corpus_sha256": reconstruction_diff.corpus_sha256(),
                "reference_sha256": sha256_file(reconstruction_diff.FUNCTION_DIR /
                                               "differential/reference.json"),
            }
            result = reconstruction_diff.qualify_candidate(gfx_revision.ROOT, commit, stage, reference)
            self.assertEqual(result["verdict"], "exact", result)
            self.assertEqual(result["scope"]["cases"], 414)

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

            mutated = root / "function-mutation"
            mutated.mkdir()
            with mock.patch.object(candidate_snapshot, "materialize_commit",
                                   side_effect=corrupt_dirty_byte):
                result = reconstruction_diff.qualify_candidate(gfx_revision.ROOT, commit, mutated, reference)
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
        self.assertTrue(gfx_revision._passes(case, result), result)
        self.assertIn(0, result["preserved_exact"])
        self.assertIn(1, result["coverage"])
