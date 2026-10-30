"""Project one sealed GEN1 raw record into ``execution-observation/v1``.

The raw ``execution_complete`` flag is not rewritten and is not fixture
qualification. A match here is receipt consistency, not proof that the
executable was instrumented. ``natural_return`` is not in the raw record.
"""
from __future__ import annotations

import hashlib

from reconstruction_trace import schema
from reconstruction_trace.tr3 import image, native, startup

_READ = native.MEMORY_READ
_WRITE = native.MEMORY_WRITE
_DROPS = ("dropped_instructions", "dropped_accesses", "dropped_transfers", "dropped_bytes")


class ProjectError(RuntimeError):
    """The raw record cannot be projected."""


def project(raw: dict, descriptor: dict, *, synthetic: bool) -> dict:
    """Return one validated observation. ``synthetic`` selects ``binary: null``."""
    try:
        _identity(raw, descriptor)
        if raw["run"]["execution_complete"] is True and not _serialized_complete(raw):
            raise ProjectError("execution_complete disagrees with the recorded instruction path")
        observation = native.observation_from_run(raw["run"], raw["qbdi_version"])
        observation["function_id"] = descriptor["function"]["name"]
        binary = descriptor["binary"]
        observation["binary"] = None if synthetic else {
            "sha256": binary["sha256"],
            "image_base": binary["image_base"],
            "entry_rva": descriptor["function"]["entry_rva"],
        }
        _note_gaps(raw, observation)
        return schema.validate(observation)
    except ProjectError:
        raise
    except (schema.TraceError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ProjectError(str(exc)) from exc


def _identity(raw: dict, descriptor: dict) -> None:
    """Reject a record whose descriptor, hashes, or instruction bytes disagree."""
    if raw.get("sealed") is not True:
        raise ProjectError("capture is not sealed")
    run = raw["run"]
    if not run.get("entered"):
        raise ProjectError(raw.get("refusal") or "entry state was not captured")
    startup.check_descriptor(descriptor, int(descriptor["binary"]["image_base"], 16))
    binary, function = descriptor["binary"], descriptor["function"]
    meta = raw["image"]
    entry = int(function["entry_rva"], 16)
    end = int(function["ranges"][0][1], 16)
    if int(meta["preferred_base"], 16) != int(binary["image_base"], 16):
        raise ProjectError("image base disagrees with the descriptor")
    if int(meta["entry_rva"], 16) != entry:
        raise ProjectError("entry RVA disagrees with the descriptor")
    mapped = bytes.fromhex(raw["mapped_window"])
    if len(mapped) != end - entry:
        raise ProjectError("range length disagrees with the mapped window")
    if hashlib.sha256(mapped).hexdigest() != binary["window_sha256"]:
        raise ProjectError("mapped window hash disagrees with the descriptor")
    for key, expected in (("file_sha256", binary["sha256"]),
                          ("expected_file_sha256", binary["sha256"]),
                          ("window_sha256", binary["window_sha256"]),
                          ("expected_window_sha256", binary["window_sha256"])):
        if raw.get(key) != expected:
            raise ProjectError(f"{key} disagrees with the descriptor")
    site = image.runtime_site(int(meta["preferred_base"], 16), int(meta["slide"], 16),
                              int(meta["entry_rva"], 16), int(meta["header"], 16))
    if site != int(meta["runtime_site"], 16):
        raise ProjectError("runtime site disagrees with the image identity")
    symbols = raw["symbols"]
    if int(symbols["scope_start"], 16) != site or int(symbols["scope_end"], 16) != site + len(mapped):
        raise ProjectError("symbol range disagrees with the mapped window")
    for inst in run["instructions"]:
        if inst.get("bytes") is None:
            continue
        offset = int(inst["pc"], 16) - site
        size = inst["inst_size"]
        recorded = bytes.fromhex(inst["bytes"])
        if offset < 0 or size != len(recorded) or mapped[offset:offset + size] != recorded:
            raise ProjectError("instruction byte disagrees with the mapped window")


def _serialized_complete(raw: dict) -> bool:
    """Observable subset of GEN1 ``capture_complete``. Not a control-flow validator.

    shortcut: natural_return is not serialized, so this cannot prove the return site.
    """
    run = raw["run"]
    if raw.get("refusal") is not None or run.get("failure") is not None:
        return False
    if not run.get("ran") or not run.get("entered") or not run.get("memory_recording"):
        return False
    if any(run.get(key) != 0 for key in _DROPS):
        return False
    if run.get("rejected_boundary") is not None or run.get("exec_transfers") != []:
        return False
    instructions = run.get("instructions") or []
    if not instructions:
        return False
    site = int(raw["image"]["runtime_site"], 16)
    end = site + len(bytes.fromhex(raw["mapped_window"]))
    expect = site
    for index, inst in enumerate(instructions):
        if int(inst["pc"], 16) != expect or not isinstance(inst.get("bytes"), str):
            return False
        if len(inst["bytes"]) != 2 * inst["inst_size"]:
            return False
        last = index + 1 == len(instructions)
        nxt = int(inst["next_pc"], 16)
        if last:
            if not inst.get("is_return"):
                return False
        elif inst.get("is_return") or not site <= nxt < end:
            return False
        for access in inst["accesses"]:
            size = access.get("size")
            if access.get("type") not in (_READ, _WRITE) or access.get("flags") != 0:
                return False
            if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 8:
                return False
            if "value" not in access:
                return False
        expect = nxt
    return True


def _note_gaps(raw: dict, observation: dict) -> None:
    """Force incompleteness when GEN1 recorded an incomplete execution."""
    run = raw["run"]
    reasons = list(observation["incomplete_reasons"])

    def add(text: str) -> None:
        if text not in reasons:
            reasons.append(text)

    if run.get("dropped_bytes"):
        add(f"byte budget dropped: {run['dropped_bytes']}")
    boundary = run.get("rejected_boundary")
    if isinstance(boundary, dict):
        add("rejected boundary: "
            f"{boundary.get('reason')} from {boundary.get('from')} at {boundary.get('pc')}")
    if raw.get("refusal"):
        add(str(raw["refusal"]))
    if run.get("execution_complete") is not True and not reasons:
        add("native execution completeness is false")
    observation["incomplete_reasons"] = reasons
    if reasons or observation["unresolved_dependencies"] or run.get("execution_complete") is not True:
        observation["completeness"] = "incomplete"
