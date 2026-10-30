#!/usr/bin/env python3
"""Offline projection of a sealed GEN1 raw record.

The committed startup receipt checks receipt consistency. It does not
authenticate an executable. The handwritten leaf is a test double. The Darwin
test is the native run. Nothing here launches EU4.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture
from reconstruction_trace.tr3 import native, project, startup

ROOT = Path(__file__).resolve().parents[1]
TR3 = ROOT / "reconstruction_trace" / "tr3"
_FORBIDDEN = ("_GfxSetConstantBuffer", "tr3_subject", "tr3_leaf_b")


def _load(name: str) -> dict:
    return json.loads((TR3 / name).read_text())


def _descriptor(receipt: dict, name: str) -> dict:
    window = receipt["window"]
    entry, end = window["entry_rva"], window["end_rva"]
    return {
        "schema": "trace-target/v1",
        "binary": {
            "sha256": receipt["expected_file_sha256"],
            "image_base": hex(window["image_base"]),
            "window_sha256": receipt["expected_window_sha256"],
        },
        "function": {"name": name, "entry_rva": hex(entry), "ranges": [[hex(entry), hex(end)]]},
        "trigger": {"mode": "first-invocation"},
        "capture": dict(startup.CEILING),
    }


def _leaf_double() -> tuple[dict, dict]:
    """One return and one stack read. Not a QBDI execution."""
    site, read = 0x100001000, 0x200000
    window = b"\xc3"
    window_hash = hashlib.sha256(window).hexdigest()
    file_hash = "ab" * 32
    registers = {name: "0x0" for name in native.ENTRY_REGISTERS}
    registers["rsp"] = hex(read)
    raw = {
        "sealed": True, "refusal": None, "qbdi_version": "test-double",
        "mapped_window": window.hex(),
        "expected_file_sha256": file_hash, "file_sha256": file_hash,
        "expected_window_sha256": window_hash, "window_sha256": window_hash,
        "image": {
            "preferred_base": "0x100000000", "slide": "0x1000", "header": "0x100001000",
            "entry_rva": "0x0", "runtime_site": hex(site),
        },
        "symbols": {"subject": hex(site), "scope_start": hex(site), "scope_end": hex(site + 1)},
        "run": {
            "failure": None, "ran": True, "entered": True, "memory_recording": True,
            "entry_gpr": {**registers, "rip": hex(site), "eflags": "0x0"},
            "exit_gpr": {"rax": "0x0"}, "regions": [], "pointers": [],
            "instructions": [{
                "pc": hex(site), "next_pc": "0x100000", "inst_size": 1, "mnemonic": "RET64",
                "disassembly": "ret", "may_load": True, "may_store": False, "load_size": 8,
                "store_size": 0, "is_call": False, "is_return": True, "bytes": "c3",
                "accesses": [{
                    "inst_address": hex(site), "address": hex(read), "value": "0x100000",
                    "size": 8, "type": native.MEMORY_READ, "flags": 0,
                }],
            }],
            "exec_transfers": [], "dropped_instructions": 0, "dropped_accesses": 0,
            "dropped_transfers": 0, "dropped_bytes": 0, "execution_complete": True,
            "rejected_boundary": None,
        },
    }
    descriptor = {
        "schema": "trace-target/v1",
        "binary": {"sha256": file_hash, "image_base": "0x100000000", "window_sha256": window_hash},
        "function": {"name": "beta", "entry_rva": "0x0", "ranges": [["0x0", "0x1"]]},
        "trigger": {"mode": "first-invocation"},
        "capture": dict(startup.CEILING),
    }
    return raw, descriptor


def _norm_snap(blob: bytes):
    regs = tuple(int.from_bytes(blob[i:i + 8], "little") for i in range(0, 128, 8))
    flags = int.from_bytes(blob[128:136], "little") & 0xCD5
    fx = blob[144:656]
    parts = [fx[0:2], fx[2:4], fx[4:5], fx[24:28]]
    for index in range(8):
        parts.append(fx[32 + 16 * index:32 + 16 * index + 10])
    parts.append(fx[160:416])
    return regs, flags, b"".join(parts)


class Gen2PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = _load("startup_receipt.json")
        cls.raw = cls.receipt["probe_output"]
        cls.desc = _descriptor(cls.receipt, "alpha")
        cls.committed = _load("startup_observation.json")

    def test_committed_startup_qualifies(self) -> None:
        raw = copy.deepcopy(self.raw)
        observation = project.project(raw, self.desc, synthetic=True)
        expected = copy.deepcopy(self.committed)
        expected["function_id"] = "alpha"
        self.assertEqual(observation, expected)
        self.assertTrue(raw["run"]["execution_complete"])
        qualified = fixture.qualify(observation)
        self.assertTrue(qualified["fixture_qualified"], qualified["reasons"])
        bound = project.project(self.raw, self.desc, synthetic=False)
        self.assertEqual(bound["binary"], {
            "sha256": self.desc["binary"]["sha256"],
            "image_base": self.desc["binary"]["image_base"],
            "entry_rva": self.desc["function"]["entry_rva"],
        })

    def test_handwritten_leaf_is_replay_incomplete(self) -> None:
        raw, descriptor = _leaf_double()
        before = copy.deepcopy(raw)
        observation = project.project(raw, descriptor, synthetic=True)
        self.assertEqual(raw, before)
        self.assertEqual(observation["function_id"], "beta")
        self.assertEqual(observation["entry_state"]["regions"], [])
        self.assertEqual(observation["entry_state"]["pointers"], [])
        self.assertTrue(observation["events"])
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertTrue(observation["unresolved_dependencies"])
        self.assertTrue(raw["run"]["execution_complete"])
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        startup_obs = project.project(self.raw, self.desc, synthetic=True)
        self.assertNotEqual(startup_obs["function_id"], observation["function_id"])
        self.assertNotEqual(
            [inst["mnemonic"] for inst in self.raw["run"]["instructions"]],
            [inst["mnemonic"] for inst in raw["run"]["instructions"]])

    def test_false_execution_complete_stays_incomplete(self) -> None:
        raw = copy.deepcopy(self.raw)
        raw["run"]["execution_complete"] = False
        observation = project.project(raw, self.desc, synthetic=True)
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertIn("native execution completeness is false", observation["incomplete_reasons"])
        self.assertFalse(raw["run"]["execution_complete"])
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])

    def test_removed_region_is_not_rediscovered(self) -> None:
        raw = copy.deepcopy(self.raw)
        cell = next(region["base"] for region in raw["run"]["regions"] if region["id"] == "cell")
        raw["run"]["regions"] = [region for region in raw["run"]["regions"] if region["id"] != "cell"]
        raw["run"]["pointers"] = [edge for edge in raw["run"]["pointers"]
                                  if edge["to"]["region"] != "cell"]
        observation = project.project(raw, self.desc, synthetic=True)
        self.assertEqual([region["id"] for region in observation["entry_state"]["regions"]], ["stack"])
        self.assertIn(cell, [item["address"] for item in observation["unresolved_dependencies"]])
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])

    def test_unknown_memory_is_not_fabricated(self) -> None:
        cases = {
            "unknown-value": lambda access: access.__setitem__("flags", native.MEMORY_UNKNOWN_VALUE),
            "wide": lambda access: access.__setitem__("size", 16),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                raw = copy.deepcopy(self.raw)
                raw["run"]["execution_complete"] = False
                access = raw["run"]["instructions"][0]["accesses"][0]
                edit(access)
                observation = project.project(raw, self.desc, synthetic=True)
                event = next(item for item in observation["events"]
                             if item["pc"] == raw["run"]["instructions"][0]["pc"])
                self.assertNotIn("value", event)
                self.assertNotEqual(event.get("value_source"), "observed")
                self.assertEqual(observation["completeness"], "incomplete")
                self.assertFalse(fixture.qualify(observation)["fixture_qualified"])

    def test_native_gaps_stay_incomplete(self) -> None:
        def boundary(reason: str, source: str):
            return lambda run: run.__setitem__(
                "rejected_boundary", {"reason": reason, "from": source, "pc": "0x1"})

        cases = {
            "instructions": lambda run: run.__setitem__("dropped_instructions", 1),
            "accesses": lambda run: run.__setitem__("dropped_accesses", 1),
            "bytes": lambda run: run.__setitem__("dropped_bytes", 1),
            "call": boundary("call", "preinst"),
            "out-of-range": boundary("out-of-range", "postinst"),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                raw = copy.deepcopy(self.raw)
                edit(raw["run"])
                raw["run"]["execution_complete"] = False
                before = copy.deepcopy(raw)
                observation = project.project(raw, self.desc, synthetic=True)
                self.assertEqual(raw, before)
                self.assertEqual(observation["completeness"], "incomplete")
                self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        contradicted = copy.deepcopy(self.raw)
        contradicted["run"]["dropped_bytes"] = 1
        with self.assertRaises(project.ProjectError) as caught:
            project.project(contradicted, self.desc, synthetic=True)
        self.assertIn("execution_complete", str(caught.exception))
        self.assertEqual(contradicted["run"]["dropped_bytes"], 1)
        self.assertTrue(contradicted["run"]["execution_complete"])

    def test_tampered_identity_is_rejected(self) -> None:
        def edit_file(raw: dict) -> None:
            raw["file_sha256"] = "ab" * 32

        def edit_window(raw: dict) -> None:
            raw["window_sha256"] = "cd" * 32

        def edit_byte(raw: dict) -> None:
            raw["run"]["instructions"][0]["bytes"] = "000000"

        def edit_entry(raw: dict) -> None:
            raw["image"]["entry_rva"] = "0x1"

        for name, edit in {
            "file": edit_file, "window": edit_window, "byte": edit_byte, "entry": edit_entry,
        }.items():
            with self.subTest(name):
                raw = copy.deepcopy(self.raw)
                edit(raw)
                with self.assertRaises(project.ProjectError):
                    project.project(raw, self.desc, synthetic=True)

    def test_malformed_and_refused_inputs_raise(self) -> None:
        with self.assertRaises(project.ProjectError):
            project.project(self.raw, {"schema": "trace-target/v1",
                                       "trigger": {"mode": "first-invocation"}}, synthetic=True)
        refused = copy.deepcopy(self.raw)
        refused["refusal"] = "rel32 out of range"
        refused["run"]["entered"] = False
        with self.assertRaises(project.ProjectError) as caught:
            project.project(refused, self.desc, synthetic=True)
        self.assertIn("rel32 out of range", str(caught.exception))
        unsealed = copy.deepcopy(self.raw)
        unsealed["sealed"] = False
        with self.assertRaises(project.ProjectError):
            project.project(unsealed, self.desc, synthetic=True)

    def test_projector_has_no_function_branch(self) -> None:
        source = Path(project.__file__).read_text()
        for name in _FORBIDDEN:
            self.assertNotIn(name, source)


class Gen2DarwinTests(unittest.TestCase):
    def setUp(self) -> None:
        reason = native.unsupported_host_reason()
        if reason:
            self.skipTest(reason)

    def test_one_dylib_projects_both_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            host, dylib = startup.build(build)
            digest = hashlib.sha256(dylib.read_bytes()).hexdigest()
            subject_proc, subject, subject_prep = self._capture(
                build, host, dylib, "_tr3_subject", legacy=True, log_name="subject.json")
            leaf_proc, leaf, leaf_prep = self._capture(
                build, host, dylib, "_tr3_leaf_b", legacy=False, log_name="leaf.json")
            self.assertEqual(hashlib.sha256(dylib.read_bytes()).hexdigest(), digest)
        self.assertEqual(subject_proc.returncode, 0, subject_proc.stderr)
        self.assertEqual(leaf_proc.returncode, 0, leaf_proc.stderr)
        self.assertTrue(subject["run"]["execution_complete"])
        self.assertTrue(leaf["run"]["execution_complete"])
        subject_obs = project.project(subject, subject_prep["descriptor"], synthetic=True)
        leaf_obs = project.project(leaf, leaf_prep["descriptor"], synthetic=True)
        self.assertEqual(subject_obs["function_id"], "tr3_subject")
        self.assertEqual(
            [region["id"] for region in subject_obs["entry_state"]["regions"]], ["cell", "stack"])
        self.assertTrue(fixture.qualify(subject_obs)["fixture_qualified"])
        self.assertEqual(leaf_obs["function_id"], "tr3_leaf_b")
        self.assertEqual(leaf_obs["entry_state"]["regions"], [])
        self.assertTrue(leaf_obs["events"])
        self.assertIn("rax", leaf_obs["entry_state"]["registers"])
        self.assertEqual(leaf_obs["completeness"], "incomplete")
        self.assertTrue(leaf["run"]["execution_complete"])
        self.assertFalse(fixture.qualify(leaf_obs)["fixture_qualified"])
        host_out = json.loads(leaf_proc.stdout)
        self.assertEqual(host_out["snaps"], 2)
        self.assertEqual(_norm_snap(bytes.fromhex(host_out["snap_exit0"])),
                         _norm_snap(bytes.fromhex(host_out["snap_exit1"])))
        self.assertNotEqual(_norm_snap(bytes.fromhex(host_out["snap_entry0"])),
                            _norm_snap(bytes.fromhex(host_out["snap_exit0"])))

    @staticmethod
    def _capture(build: Path, host: Path, dylib: Path, symbol: str, *, legacy: bool, log_name: str):
        prepared = startup.prepare_target(host, symbol, startup.CEILING)
        log = build / log_name
        env = startup.target_env(dylib, log, prepared["descriptor"], preferred=prepared["preferred"],
                                 legacy=legacy)
        proc, raw = startup.acquire(host, env, log)
        return proc, raw, prepared


if __name__ == "__main__":
    unittest.main()
