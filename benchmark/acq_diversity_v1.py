"""Independent acq-diversity/v1 reference. Not a copy of the native collector.

The admission word is an integer here. Scripted barriers stand in for the
interleavings the contract requires to be exact; an unscripted race is not a
counter oracle. shortcut: integer word, upgrade when a native trace is diffed.
"""
from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oracle.certify import certify as e1  # noqa: E402
from oracle.certify.errors import CertifyError  # noqa: E402
from oracle.certify.evidence import Admission, Transcript  # noqa: E402
from oracle.certify.policy import behavior, load_policy  # noqa: E402
from oracle.function import fixture, synthetic  # noqa: E402

SUMMARY_V2 = "EU4-GFX-LIVE-SUMMARY v2"
AUDIT_SCHEMA = "eu4-gfx-live-audit-associations/v1"
MANIFEST_V2 = "eu4-gfx-live-manifest/v2"
POLICY_NAME = "acq-diversity/v1"
BUF = 0xFFFFFFFF
TABLE = 128
QUOTA = 64
GATE_ATTEMPTS = 8
READ_ATTEMPTS = 8
REQUIRED = (
    "slot:0", "slot:1", "slot:2", "slot:3", "slot:4", "slot:5",
    "selector:fixed-length", "selector:length-from-buffer",
    "input:absent", "input:zero", "input:nonzero",
)
DIAG_FIELDS = (
    "intercepted", "invalid_refused", "pending_contended",
    "verification_contended", "screened_repeat", "selected", "busy", "slot",
    "pointer", "address", "thread_limit", "closed", "read_failed",
    "signature_read_contended", "key_insert_contended", "table_capacity_refused",
    "capture_buffer_refused", "capacity_refused_selection", "cas_exhausted",
    "first_selected", "verification_selected", "novel_retained",
    "producer_matched_repeat", "distinct_not_retained",
    "ineligible_not_established", "candidate_incomplete", "storage_failed",
    "undrained", "selected_invariant_failure", "producer_matched_discarded",
    "audit_persisted", "admission_invariant_lost", "unanchored_reclaimed",
    "p_cov", "p_div", "r", "l",
)
_REFUSAL = (
    "busy", "slot", "pointer", "address", "thread_limit", "closed",
    "read_failed", "signature_read_contended", "key_insert_contended",
    "table_capacity_refused", "capture_buffer_refused",
    "capacity_refused_selection", "cas_exhausted",
)
_SELECTED_PARTS = (
    "novel_retained", "producer_matched_repeat", "distinct_not_retained",
    "ineligible_not_established", "candidate_incomplete", "storage_failed",
    "undrained", "selected_invariant_failure",
)


def lower_bound(remaining: set[str]) -> int:
    """L for cells neither established nor held by an in-flight reservation."""
    need_fixed = int(
        "selector:fixed-length" in remaining or "input:absent" in remaining)
    need_zero = int("input:zero" in remaining)
    need_nonzero = int("input:nonzero" in remaining)
    need_buffer = int(
        "selector:length-from-buffer" in remaining
        and need_zero == 0 and need_nonzero == 0)
    need_slots = sum(f"slot:{i}" in remaining for i in range(6))
    return max(need_slots, need_fixed + need_zero + need_nonzero + need_buffer)


def project(slot: int, selector: int, input_value: int) -> set[str]:
    cells = {f"slot:{slot}"}
    if selector == BUF:
        cells.add("selector:length-from-buffer")
        cells.add("input:zero" if input_value == 0 else "input:nonzero")
    else:
        cells.add("selector:fixed-length")
        cells.add("input:absent")
    return cells


def build_transcript(descriptor: dict, *, slot: int, r9d: int,
                     buffer_dword: int | None, words: int = 4,
                     sequence: int = 1, scenario: str = "acq:variant") -> str:
    """Test-only transcript. Callers must run fixture.assess themselves."""
    spec = {
        "slot": slot, "r9d": r9d, "buffer_dword": buffer_dword, "words": words,
        "buffer_path": r9d == BUF, "sequence": sequence, "scenario": scenario,
    }
    regions = synthetic._regions(descriptor, spec)
    carriers = {"RDI": synthetic.HANDLE, "RSI": synthetic.BUFFER,
                "RCX": slot, "R9": r9d}
    lines = [
        f"{fixture.CAPTURE_HEADER} {fixture.CAPTURE_VERSION}",
        "CAPTURE "
        f"capture=acq-model scenario={scenario} epoch=synthetic-a2 "
        f"thread=0 sequence={sequence} function={descriptor['function_id']} "
        f"callsite=unavailable module={descriptor['binary']['sha256']} "
        f"policy={fixture.capture_policy_digest(descriptor)} "
        f"fidelity={descriptor['fidelity']['mode']} "
        f"policy_version={descriptor['fidelity']['policy_version']} synthetic=1",
    ]
    for phase, tag in (("pre", "ENTRY"), ("post", "EXIT")):
        for name in fixture.REGION_ORDER:
            if name not in regions:
                continue
            address, pre, post = regions[name]
            data = pre if phase == "pre" else post
            lines.append(
                f"{tag} name={name} address={hex(address)} bytes={data.hex()}")
    for name, value in carriers.items():
        bits = next(item["bits"] for item in descriptor["abi"]["required_carriers"]
                    if item["name"] == name)
        lines.append(
            f"CARRIER name={name} kind=int bits={bits} value={hex(value)} "
            f"confidence=observed")
    lines.append(f"OUTCOME register=RAX value={hex(synthetic.CTX)} confidence=observed")
    lines.append(
        f"END regions={2 * len(regions)} carriers={len(carriers)} outcomes=1 losses=0")
    return "\n".join(lines) + "\n"


def _qualified(text: str, descriptor: dict) -> tuple[bool, dict | None]:
    try:
        body = fixture.materialize_capture(text, descriptor)
    except fixture.FixtureError:
        return False, None
    record = body["record"]
    missing = record["completeness"]["unavailable_dependencies"]
    losses = record["completeness"]["losses"]
    return not missing and losses == 0 and body["manifest"]["verdict"] == "qualified", record


def _fingerprint(record: dict, descriptor: dict, input_value: int) -> tuple:
    item = behavior(record, descriptor)
    spans = []
    for phase in ("pre", "post"):
        for span in record["state"][phase]:
            spans.append((phase, span["space"], span["value_hex"]))
    parts = {piece.split("=", 1)[0]: piece.split("=", 1)[1] for piece in item.key}
    return (input_value, tuple(spans), parts["alias"], parts["pointer"],
            parts["rax"], parts["canary"])


def _screening_key(text: str, descriptor: dict) -> tuple:
    parsed = fixture.parse_capture(text)
    carriers = {item["name"]: item["value"] for item in parsed["carriers"].values()}
    slot = carriers["RCX"] & 0xFFFFFFFF
    selector = carriers["R9"] & 0xFFFFFFFF
    if selector == descriptor["input_regions"]["buffer"]["selector_value"]:
        region = next(item for item in parsed["regions"]
                      if item["phase"] == "pre" and item["name"] == "buffer")
        input_value = int.from_bytes(region["bytes"][:4], "little")
    else:
        input_value = selector
    return (parsed["capture"]["function"], slot, selector, input_value)


class Entry:
    def __init__(self) -> None:
        self.state = "EMPTY"
        self.key: tuple | None = None
        self.generation = 0
        self.e = 0
        self.u = 0
        self.verify_held = False
        self.reprobe_held = False
        self.anchor: tuple | None = None
        self.behavior_key: tuple | None = None
        self.input_value: int | None = None


class Session:
    def __init__(self, descriptor: dict | None = None) -> None:
        self.descriptor = descriptor or fixture.load_descriptor()
        self.entries = [Entry() for _ in range(TABLE)]
        self.established: set[str] = set()
        self.reserved: set[str] = set()
        self.p_cov = 0
        self.p_div = 0
        self.r = 0
        self.buffers = QUOTA
        self.gate_held = False
        self.stop_selection = False
        self.unstable_reads = 0
        self.unreadable: set[int] = set()
        self.admission_lost = False
        self.retained_inputs: dict[tuple, list[int]] = {}
        self.retained_texts: list[str] = []
        self.audit_pairs: list[tuple[str, str]] = []
        self.ineligible_files = 0
        self.counts = {name: 0 for name in DIAG_FIELDS}
        self.outcomes: list[str] = []
        self._paused: dict | None = None

    def l_value(self) -> int:
        held = self.established | self.reserved
        return lower_bound(set(REQUIRED) - held)

    def snapshot(self) -> dict[str, int]:
        counts = dict(self.counts)
        counts["invalid_refused"] = sum(counts[name] for name in _REFUSAL)
        counts["producer_matched_repeat"] = (
            counts["producer_matched_discarded"] + counts["audit_persisted"])
        counts["selected"] = counts["first_selected"] + counts["verification_selected"]
        counts["intercepted"] = (
            counts["invalid_refused"] + counts["pending_contended"]
            + counts["verification_contended"] + counts["screened_repeat"]
            + counts["selected"])
        counts["p_cov"] = self.p_cov
        counts["p_div"] = self.p_div
        counts["r"] = self.r
        counts["l"] = self.l_value()
        counts["admission_invariant_lost"] = int(self.admission_lost)
        counts["unanchored_reclaimed"] = self.counts["unanchored_reclaimed"]
        return counts

    def identities_hold(self) -> bool:
        got = self.snapshot()
        selected_parts = sum(got[name] for name in _SELECTED_PARTS)
        return (got["selected"] == selected_parts
                and got["novel_retained"] == got["r"] <= QUOTA
                and got["audit_persisted"] <= 8
                and self.ineligible_files <= 11
                and self.r + self.p_cov + self.p_div + self.l_value() <= QUOTA)

    def diag(self, scenario: str, variant: str | None = None,
             outcome: str | None = None) -> str:
        parts = [f"ACQ_DIAG scenario={scenario}"]
        if variant is not None:
            parts.append(f"variant={variant}")
        if outcome is not None:
            parts.append(f"outcome={outcome}")
        got = self.snapshot()
        parts.extend(f"{name}={got[name]}" for name in DIAG_FIELDS)
        return " ".join(parts)

    def _bump(self, name: str) -> str:
        self.counts[name] += 1
        self.outcomes.append(name)
        return name

    def _find(self, key: tuple) -> Entry | None:
        for entry in self.entries:
            if entry.state != "EMPTY" and entry.key == key:
                return entry
        return None

    def _lookup_failed(self) -> str | None:
        if self.unstable_reads >= READ_ATTEMPTS:
            return self._bump("signature_read_contended")
        if any(entry.generation % 2 == 1 for entry in self.entries):
            return self._bump("signature_read_contended")
        return None

    def _priority(self, cells: set[str]) -> set[str]:
        return {cell for cell in cells
                if cell in REQUIRED and cell not in self.established
                and cell not in self.reserved}

    def _schedule(self, counter: int, cells: set[str]) -> bool:
        if counter == 2 or (counter > 2 and (counter - 1) % 16 == 0):
            return True
        return bool(self._priority(cells))

    def _reserve(self, cells: set[str]) -> dict | str:
        fresh = self._priority(cells)
        kind = "cov" if fresh else "div"
        extra_cov = 1 if kind == "cov" else 0
        extra_div = 1 if kind == "div" else 0
        later = lower_bound(set(REQUIRED) - self.established - self.reserved - fresh)
        if self.r + self.p_cov + extra_cov + self.p_div + extra_div + later > QUOTA:
            return "capacity_refused_selection"
        if kind == "cov":
            self.p_cov += 1
            self.reserved.update(fresh)
        else:
            self.p_div += 1
        return {"kind": kind, "cells": fresh}

    def _rollback(self, reservation: dict, *, clear: bool) -> bool:
        if not clear:
            self.admission_lost = True
            self.stop_selection = True
            return False
        if reservation["kind"] == "cov":
            self.p_cov -= 1
            self.reserved.difference_update(reservation["cells"])
        else:
            self.p_div -= 1
        return True

    def _commit_retain(self, reservation: dict) -> None:
        if reservation["kind"] == "cov":
            self.p_cov -= 1
            for cell in reservation["cells"]:
                self.reserved.discard(cell)
                self.established.add(cell)
        else:
            self.p_div -= 1
        self.r += 1

    def _idle_victim(self) -> Entry | None:
        for entry in self.entries:
            if (entry.state == "UNANCHORED" and not entry.reprobe_held
                    and entry.generation % 2 == 0):
                return entry
        return None

    def _publish(self, key: tuple, victim: Entry | None) -> Entry:
        entry = victim or next(item for item in self.entries if item.state == "EMPTY")
        if victim is not None:
            self.counts["unanchored_reclaimed"] += 1
        entry.state = "PENDING"
        entry.key = key
        entry.generation += 2
        entry.e = 0
        entry.u = 0
        entry.verify_held = False
        entry.reprobe_held = False
        entry.anchor = None
        return entry

    def _finish_selected(self, entry: Entry, key: tuple, text: str,
                         reservation: dict, existing: bool, fault: str | None) -> str:
        qualified, record = _qualified(text, self.descriptor)
        input_value = key[3]
        if fault == "missing_reservation":
            self._rollback(reservation, clear=True)
            name = "selected_invariant_failure"
        elif fault == "post_bind_mismatch":
            self._rollback(reservation, clear=True)
            self.admission_lost = True
            self.stop_selection = True
            name = "selected_invariant_failure"
        elif fault == "incomplete":
            self._rollback(reservation, clear=True)
            if entry.state == "PENDING":
                entry.state = "EMPTY"
                entry.key = None
            name = "candidate_incomplete"
        elif fault == "storage_failed":
            self._rollback(reservation, clear=True)
            name = "storage_failed"
        elif fault == "ineligible" or not qualified or record is None:
            self._rollback(reservation, clear=True)
            if self.ineligible_files < 11:
                self.ineligible_files += 1
            if entry.state == "PENDING":
                entry.state = "UNANCHORED"
                entry.u = 1
            name = "ineligible_not_established"
        else:
            item = behavior(record, self.descriptor)
            finger = _fingerprint(record, self.descriptor, input_value)
            if existing and entry.anchor == finger:
                self._rollback(reservation, clear=True)
                if self.counts["audit_persisted"] < 8:
                    self.audit_pairs.append((text, text))
                    name = "audit_persisted"
                else:
                    name = "producer_matched_discarded"
            else:
                designated = reservation["kind"] == "cov"
                seen = self.retained_inputs.setdefault(item.key, [])
                if (not designated and input_value in seen) or (
                        not designated and len(seen) >= 2):
                    self._rollback(reservation, clear=True)
                    name = "distinct_not_retained"
                    if entry.state == "PENDING":
                        entry.state = "UNANCHORED"
                        entry.u = 1
                else:
                    if input_value not in seen:
                        seen.append(input_value)
                    self._commit_retain(reservation)
                    self.retained_texts.append(text)
                    entry.state = "ANCHORED"
                    entry.anchor = finger
                    entry.behavior_key = item.key
                    entry.input_value = input_value
                    if entry.e == 0:
                        entry.e = 1
                    name = "novel_retained"
        if existing:
            self.counts["verification_selected"] += 1
        else:
            self.counts["first_selected"] += 1
        entry.verify_held = False
        entry.reprobe_held = False
        self.buffers += 1
        return self._bump(name)

    def invoke(self, text: str, fault: str | None = None) -> str:
        if fault == "read_failed":
            return self._bump("read_failed")
        failed = self._lookup_failed()
        if failed:
            return failed
        key = _screening_key(text, self.descriptor)
        cells = project(key[1], key[2], key[3])
        entry = self._find(key)
        if entry is not None and entry.state == "PENDING":
            return self._bump("pending_contended")
        if entry is not None and entry.state == "ANCHORED":
            if entry.verify_held:
                return self._bump("verification_contended")
            entry.e += 1
            if self.stop_selection or not self._schedule(entry.e, cells):
                return self._bump("screened_repeat")
            return self._phase_b(entry, key, text, cells, True, fault)
        if entry is not None and entry.state == "UNANCHORED":
            if entry.reprobe_held:
                return self._bump("verification_contended")
            entry.u += 1
            if self.stop_selection or not self._schedule(entry.u, cells):
                return self._bump("screened_repeat")
            return self._phase_b(entry, key, text, cells, True, fault)
        if self.stop_selection:
            return self._bump("cas_exhausted")
        return self._phase_b(None, key, text, cells, False, fault)

    def _phase_b(self, entry: Entry | None, key: tuple, text: str, cells: set[str],
                 existing: bool, fault: str | None) -> str:
        if self.buffers <= 0:
            return self._bump("capture_buffer_refused")
        self.buffers -= 1
        if fault == "admission_cas_fail":
            self.buffers += 1
            return self._bump("cas_exhausted")
        reserved = self._reserve(cells)
        if reserved == "capacity_refused_selection":
            self.buffers += 1
            return self._bump("capacity_refused_selection")
        assert isinstance(reserved, dict)
        if existing:
            assert entry is not None
            if entry.state == "ANCHORED":
                entry.verify_held = True
            else:
                entry.reprobe_held = True
            if fault == "hold_reservation":
                return "held"
            if fault == "rollback_fail":
                self.buffers += 1
                self._rollback(reserved, clear=False)
                return self._bump("cas_exhausted")
            return self._finish_selected(entry, key, text, reserved, True, fault)
        if self.unreadable or self.gate_held:
            self.buffers += 1
            self._rollback(reserved, clear=True)
            return self._bump("cas_exhausted")
        if any(item.state != "EMPTY" and item.key == key for item in self.entries):
            self.buffers += 1
            self._rollback(reserved, clear=True)
            return self._bump("key_insert_contended")
        if not any(item.state == "EMPTY" for item in self.entries):
            victim = self._idle_victim()
            if victim is None:
                self.buffers += 1
                self._rollback(reserved, clear=True)
                return self._bump("table_capacity_refused")
        else:
            victim = None
        if fault == "rollback_fail":
            self.buffers += 1
            self._rollback(reserved, clear=False)
            return self._bump("cas_exhausted")
        published = self._publish(key, victim)
        if fault == "leave_pending":
            return "pending"
        return self._finish_selected(published, key, text, reserved, False, fault)

    def pause_before_gate(self, text: str) -> None:
        """B has finished Phase A while the key is absent and has not taken the gate."""
        key = _screening_key(text, self.descriptor)
        cells = project(key[1], key[2], key[3])
        self.buffers -= 1
        reserved = self._reserve(cells)
        assert isinstance(reserved, dict)
        self._paused = {"key": key, "text": text, "cells": cells, "reservation": reserved}

    def complete_paused(self) -> str:
        paused = self._paused
        assert paused is not None
        self._paused = None
        key = paused["key"]
        if any(item.state != "EMPTY" and item.key == key for item in self.entries):
            self.buffers += 1
            self._rollback(paused["reservation"], clear=True)
            return self._bump("key_insert_contended")
        self.buffers += 1
        self._rollback(paused["reservation"], clear=True)
        return self._bump("cas_exhausted")

    def inject_unanchored(self, count: int = TABLE) -> None:
        """Reachable full table: idle UNANCHORED entries, not live PENDING."""
        function = self.descriptor["function_id"]
        for index in range(count):
            entry = self.entries[index]
            entry.state = "UNANCHORED"
            entry.key = (function, 0, 1, index + 1)
            entry.generation = 2
            entry.u = 1

    def inject_pending_full(self) -> None:
        """Synthetic state injection. 128 PENDING rows violate R + P + L ≤ 64."""
        function = self.descriptor["function_id"]
        for index, entry in enumerate(self.entries):
            entry.state = "PENDING"
            entry.key = (function, 0, 2, index + 1)
            entry.generation = 2


def s03_text(descriptor: dict | None = None) -> str:
    return synthetic.capture_text(descriptor or fixture.load_descriptor(), "s03")


def run_shared(scenario: str, variant: str | None = None) -> str:
    """Print one ACQ_DIAG line for a shared scenario. Returns the line."""
    descriptor = fixture.load_descriptor()
    base = s03_text(descriptor)
    session = Session(descriptor)
    outcome = None
    if scenario == "identical_calls_100":
        for _ in range(100):
            session.invoke(base)
    elif scenario == "same_slot_different_dword":
        other = build_transcript(descriptor, slot=2, r9d=BUF, buffer_dword=1)
        session.invoke(base)
        session.invoke(other)
    elif scenario == "scheduled_sample_phase_b_refused":
        variant = "cas_exhausted"
        session.invoke(base)
        outcome = session.invoke(base, fault="admission_cas_fail")
    elif scenario == "unanchored_missing_cell_pcov":
        session.invoke(base, fault="ineligible")
        outcome = session.invoke(base)
    elif scenario == "two_threads_same_unseen_key":
        if variant == "holds_gate":
            session.gate_held = True
            outcome = session.invoke(base)
        elif variant == "sees_pending":
            session.invoke(base, fault="leave_pending")
            outcome = session.invoke(base)
        elif variant == "key_insert_contended":
            session.pause_before_gate(base)
            session.invoke(base, fault="leave_pending")
            outcome = session.complete_paused()
        else:
            raise ValueError(variant)
    elif scenario == "anchor_survives_nonretained":
        faults = {2: "not_match", 17: "ineligible", 33: "storage_failed"}
        # Encounter 2 must be a qualified non-match, not an eligibility failure.
        mutated = _mutate_canary(base)
        for number in range(1, 34):
            fault = faults.get(number)
            text = mutated if fault == "not_match" else base
            if fault == "not_match":
                session.invoke(text)
            else:
                session.invoke(base, fault=None if fault is None else fault)
        variant = variant or "encounter_33"
    elif scenario == "rollback_fail_after_bind_fail":
        outcome = session.invoke(base, fault="rollback_fail")
    else:
        raise ValueError(scenario)
    line = session.diag(scenario, variant=variant, outcome=outcome)
    print(line)
    return line


def _mutate_canary(text: str) -> str:
    """Change both canary images together so closure still sees preservation."""
    parsed = fixture.parse_capture(text)
    original = next(item["bytes"] for item in parsed["regions"]
                    if item["name"] == "canary-before")
    flipped = bytes(byte ^ 0xFF for byte in original)
    return text.replace(original.hex(), flipped.hex())


def _read_table(path: Path, header: str) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != header or lines[-1] != "END":
        raise ValueError(f"{path.name} is not a {header} table")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        key, sep, value = line.partition("=")
        if not sep or key in fields:
            raise ValueError(f"{path.name} has a bad field line")
        fields[key] = value
    return fields


def _file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def structural_problems(session_dir: Path) -> list[str]:
    """Re-read a sealed v2 session. The reporter verdict is not an input."""
    session_dir = Path(session_dir)
    problems: list[str] = []
    try:
        summary = _read_table(session_dir / "summary.native", SUMMARY_V2)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if summary.get("acquisition_policy") != POLICY_NAME:
        problems.append("acquisition_policy is not acq-diversity/v1")
    numbers: dict[str, int] = {}
    for name in DIAG_FIELDS:
        raw = summary.get(name)
        if raw is None or not raw.isascii() or not raw.isdigit():
            problems.append(f"missing counter {name}")
            continue
        numbers[name] = int(raw)
    if numbers:
        invalid = sum(numbers.get(name, 0) for name in _REFUSAL)
        selected = numbers.get("first_selected", 0) + numbers.get("verification_selected", 0)
        parts = sum(numbers.get(name, 0) for name in _SELECTED_PARTS)
        intercepted = (invalid + numbers.get("pending_contended", 0)
                       + numbers.get("verification_contended", 0)
                       + numbers.get("screened_repeat", 0) + selected)
        if numbers.get("invalid_refused") != invalid:
            problems.append("invalid_refused identity failed")
        if numbers.get("selected") != selected or selected != parts:
            problems.append("selected identity failed")
        repeat = (numbers.get("producer_matched_discarded", 0)
                  + numbers.get("audit_persisted", 0))
        if numbers.get("producer_matched_repeat") != repeat:
            problems.append("producer_matched_repeat identity failed")
        if numbers.get("intercepted") != intercepted:
            problems.append("intercepted identity failed")
        if numbers.get("novel_retained") != numbers.get("r"):
            problems.append("novel_retained is not R")
        if numbers.get("storage_failed") or numbers.get("undrained") or numbers.get(
                "admission_invariant_lost"):
            problems.append("structural loss counters are nonzero")
        if numbers.get("audit_persisted", 0) > 8:
            problems.append("audit cap exceeded")
    try:
        manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        problems.append("manifest.json is unreadable")
        manifest = {}
    if manifest.get("schema") != MANIFEST_V2:
        problems.append("manifest schema is not v2")
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    if "audit-associations.json" not in files:
        problems.append("manifest does not hash audit-associations.json")
    for rel, digest in files.items():
        path = session_dir / rel
        if not path.is_file() or _file_sha(path) != digest:
            problems.append(f"manifest hash mismatch for {rel}")
    try:
        audit = json.loads(
            (session_dir / "audit-associations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        problems.append("audit-associations.json is unreadable")
        audit = {}
    if audit.get("schema") != AUDIT_SCHEMA or not isinstance(audit.get("entries"), list):
        problems.append("audit-associations.json schema is wrong")
    retained = [name for name in files
                if name.startswith("captures/") and name.endswith(".capture")
                and ".audit." not in name and ".ineligible." not in name]
    seen_audits: set[str] = set()
    descriptor = fixture.load_descriptor()
    qualified_cells: set[str] = set()
    for name in retained:
        text = (session_dir / name).read_text(encoding="utf-8")
        ok, record = _qualified(text, descriptor)
        if ok and record is not None:
            qualified_cells.update(
                cell for cell in behavior(record, descriptor).cells if cell in REQUIRED)
    claimed = {cell for cell in summary.get("established", "").split(",") if cell}
    if not claimed <= qualified_cells:
        problems.append("native coverage is not a subset of qualified retained cells")
    for row in audit.get("entries") or []:
        if not isinstance(row, dict):
            problems.append("audit row is not an object")
            continue
        audit_path = row.get("audit_path")
        reference = row.get("reference_path")
        if audit_path in seen_audits:
            problems.append("duplicate audit path")
        seen_audits.add(str(audit_path))
        if audit_path not in files or reference not in retained:
            problems.append("audit reference is outside retained captures")
            continue
        audit_file = session_dir / str(audit_path)
        reference_file = session_dir / str(reference)
        if _file_sha(audit_file) != row.get("audit_sha256") or _file_sha(
                reference_file) != row.get("reference_sha256"):
            problems.append("audit row hash mismatch")
        try:
            audit_text = audit_file.read_text(encoding="utf-8")
            reference_text = reference_file.read_text(encoding="utf-8")
            ok_a, rec_a = _qualified(audit_text, descriptor)
            ok_b, rec_b = _qualified(reference_text, descriptor)
            if not (ok_a and ok_b and rec_a and rec_b):
                problems.append("audit transcripts are not qualified")
            else:
                key_a = _screening_key(audit_text, descriptor)
                if _fingerprint(rec_a, descriptor, key_a[3]) != _fingerprint(
                        rec_b, descriptor, key_a[3]):
                    problems.append("audit producer match disagrees")
        except (OSError, fixture.FixtureError):
            problems.append("audit transcripts are unreadable")
    return problems


def report_v2(run_dir: Path) -> dict[str, Any]:
    """Validate one v2 session. Does not admit a v1 summary and does not launch."""
    problems = structural_problems(run_dir)
    status = "admitted" if not problems else "refused"
    result = {
        "schema": "eu4-gfx-live-session/v2",
        "run_dir": str(run_dir),
        "status": status,
        "structural": status,
        "problems": problems,
    }
    (run_dir / "session.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def certify_e1_v2(session_dir: Path, descriptor: dict, policy: Path,
                  candidate: dict, *, certificate_dir: Path) -> dict[str, Any]:
    """Certify retained v2 evidence. Structural refusal is not a promotion status."""
    session_dir = Path(session_dir).expanduser().resolve()
    certificate_dir = Path(certificate_dir).expanduser().resolve()
    problems = structural_problems(session_dir)
    if problems:
        return {"structural": "refused", "problems": problems, "certificate": None}
    e1._reject_certificate_inside_session(session_dir, certificate_dir)
    file_sha = e1._descriptor_file_sha256(descriptor)
    loaded = load_policy(Path(policy), descriptor, file_sha)
    manifest_sha = sha256((session_dir / "manifest.json").read_bytes()).hexdigest()
    transcripts: list[Transcript] = []
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    for name in sorted(manifest["files"]):
        if not (name.startswith("captures/") and name.endswith(".capture")
                and ".audit." not in name and ".ineligible." not in name):
            continue
        path = session_dir / name
        text = path.read_text(encoding="utf-8")
        ok, _record = _qualified(text, descriptor)
        if not ok:
            continue
        body = fixture.materialize_capture(text, descriptor)
        transcripts.append(Transcript(
            name=name, text=text, capture=fixture.parse_capture(text)["capture"],
            record=body["record"], manifest=body["manifest"]))
    rows = tuple(transcripts)
    admission = Admission(
        eligible=True, reasons=(), fixtures="eligible", fixture_reasons=(),
        transcripts=rows, eligible_transcripts=rows, transcript_count=len(rows),
        ineligible=0, malformed=0,
        loss={"write_failed": 0, "undrained": 0, "incomplete": 0},
        manifest_sha256=manifest_sha, library_sha256=None)
    binding = e1._bind_candidate(candidate)
    records = [item.record for item in rows]
    coverage_body, coverage_error = e1._coverage_or_failure(records, descriptor, loaded)
    counts = {"match": 0, "mismatch": 0, "infrastructure_refusal": 0,
              "evidence_refusal": 0}
    failures: list[str] = []
    replayed = False
    executed = False
    if binding.ok and binding.harness_path is not None and binding.executable_sha256:
        replayed = True
        before = binding.executable_sha256
        from oracle.function import replay
        for transcript in rows:
            try:
                result = replay.replay_capture(transcript.text, binding.harness_path)
            except Exception as error:  # one fixture must not hide the rest
                result = {"verdict": "refused", "failures": [
                    f"reference:replay-error: {type(error).__name__}: {error}"]}
            kind = e1._classify(result)
            counts[kind] += 1
            if kind != "match":
                failures.extend(f"{transcript.name}: {item}"
                                for item in (result.get("failures") or [kind]))
        executed = e1._file_sha256(binding.harness_path) == before
    else:
        failures.extend(binding.failures)
    status, reasons = e1._promotion(
        admission, binding, counts, coverage_body, coverage_error,
        executed_matches_build=executed, replayed=replayed)
    if not coverage_body:
        coverage_body = {"missing_required": list(loaded.required_cells),
                         "diversity_met": False}
    limitations = ["live grounding is not established; the certifier checks "
                   "recorded identity and hash consistency only",
                   "v2 evidence does not upgrade sealed-session-unverified"]
    explicitly = any(item.capture.get("synthetic") for item in rows)
    tier = "explicitly-synthetic" if explicitly else "sealed-session-unverified"
    if tier == "tier1-live-sealed-session":
        raise CertifyError("refusing to claim a live-grounded evidence tier")
    certificate = {
        "schema": e1.SCHEMA,
        "function_id": descriptor["function_id"],
        "evidence_tier": tier,
        "evidence": {
            "session_id": manifest.get("session"),
            "manifest_sha256": manifest_sha,
            "descriptor_sha256": file_sha,
            "policy_sha256": loaded.sha256,
            "transcript_count": len(rows),
            "eligible": len(rows),
            "live_grounded": False,
            "evidence_label": "synthetic" if explicitly else "unverified",
        },
        "candidate": {
            "source_sha256": binding.source_sha256,
            "executable_sha256": binding.executable_sha256,
            "build_identity": binding.build_identity,
            "build_flags": list(binding.build_flags),
            "executed_matches_build": executed,
        },
        "evaluation": counts | {"failures": failures},
        "coverage": {key: value for key, value in coverage_body.items()
                     if not str(key).startswith("_")},
        "promotion": {"status": status, "reasons": reasons},
        "limitations": limitations,
    }
    certificate_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(certificate, sort_keys=True, indent=2) + "\n"
    e1._publish_certificate_atomically(
        certificate_dir, certificate_dir / e1.CERTIFICATE_NAME, text)
    return {"structural": "admitted", "problems": [], "certificate": certificate}
