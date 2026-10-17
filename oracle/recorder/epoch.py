"""R0-M protocol compatibility for the P4 recorder, validated offline.

Every observation is attributed to an **epoch**: the R0-M measurement epoch the
capture ran under. R0-M identifies an epoch by a single 64-bit control word
published through a seqlock page - version, mode, scenario id, generation - and the
Oracle must agree with that protocol exactly, without launching anything.

This module decodes the control word itself (the Oracle's own reading of the
protocol), derives the epoch and scenario tokens a record's provenance must carry,
and verifies a multi-epoch capture offline:

* every control word is decodable, and generations strictly increase;
* no record is attributed to a passive epoch - passive means "observe nothing";
* every record's ``provenance.epoch`` and ``provenance.scenario`` are the tokens of a
  capture-enabled epoch in the sequence.

The R0-M mirror in ``benchmark/eu4_r0_control.py`` is the protocol's implementation.
A test cross-checks this decoder against it field by field, so compatibility is a
checked claim rather than a copied constant. Nothing here opens, connects to, or
observes EU IV.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Sequence

CONTROL_VERSION = 1

MODE_PASSIVE = 0
MODE_CENSUS = 1
MODE_CENSUS_CAPTURE = 2
VALID_MODES = (MODE_PASSIVE, MODE_CENSUS, MODE_CENSUS_CAPTURE)

GENERATION_MASK = 0xFFFFFFFF
SCENARIO_MASK = 0xFFFF
MODE_MASK = 0xFF
VERSION_MASK = 0xFF
SCENARIO_SHIFT = 32
MODE_SHIFT = 48
VERSION_SHIFT = 56


class EpochError(ValueError):
    """A capture is not compatible with the R0-M epoch protocol."""


class Epoch(NamedTuple):
    """One R0-M control-word epoch."""

    generation: int
    mode: int
    scenario_id: int
    version: int

    @property
    def capture_enabled(self) -> bool:
        """Whether this epoch observes anything at all; passive observes nothing."""
        return self.mode != MODE_PASSIVE

    @property
    def token(self) -> str:
        """The ``provenance.epoch`` a record captured under this epoch must carry."""
        return f"r0m-v{self.version}-g{self.generation}"

    @property
    def scenario_token(self) -> str:
        """The ``provenance.scenario`` a record captured under this epoch must carry."""
        return f"r0m-s{self.scenario_id}"


def unpack_control_word(word: int) -> Epoch | None:
    """Decode an R0-M control word, or ``None`` when it is not a valid one.

    A word with the wrong version or an unknown mode is invalid - exactly the words
    R0-M's own reader rejects - so the recorder never attributes an observation to an
    epoch it cannot read.
    """
    generation = word & GENERATION_MASK
    scenario_id = (word >> SCENARIO_SHIFT) & SCENARIO_MASK
    mode = (word >> MODE_SHIFT) & MODE_MASK
    version = (word >> VERSION_SHIFT) & VERSION_MASK
    if version != CONTROL_VERSION or mode not in VALID_MODES:
        return None
    return Epoch(generation, mode, scenario_id, version)


def epochs(control_words: Sequence[int]) -> list[Epoch]:
    """Decode a control-word sequence, enforcing the protocol's ordering rule."""
    decoded: list[Epoch] = []
    previous: int | None = None
    for word in control_words:
        epoch = unpack_control_word(int(word))
        if epoch is None:
            raise EpochError(f"control word {int(word):#018x} is not a valid R0-M word")
        if previous is not None and epoch.generation <= previous:
            raise EpochError(
                f"R0-M generations must strictly increase: {epoch.generation} after {previous}")
        previous = epoch.generation
        decoded.append(epoch)
    return decoded


def _provenance(record: dict[str, Any]) -> dict[str, Any]:
    """The provenance of a sealed record or a bare payload."""
    return record.get("payload", record)["provenance"]


def check_compatibility(control_words: Sequence[int],
                        records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Attribute every record to an epoch of the R0-M sequence, or raise."""
    sequence = epochs(control_words)
    enabled = {epoch.token: epoch for epoch in sequence if epoch.capture_enabled}
    passive = {epoch.token for epoch in sequence if not epoch.capture_enabled}
    counts = {token: 0 for token in enabled}

    for index, record in enumerate(records):
        provenance = _provenance(record)
        token = provenance["epoch"]
        scenario = provenance["scenario"]
        if token in passive:
            raise EpochError(
                f"record {index} is attributed to the passive epoch {token}, which "
                "captures nothing")
        if token not in enabled:
            raise EpochError(
                f"record {index} names epoch {token}, which is not in the R0-M sequence")
        expected = enabled[token].scenario_token
        if scenario != expected:
            raise EpochError(
                f"record {index} names scenario {scenario} but epoch {token} declares {expected}")
        counts[token] += 1

    return {
        "schema": "eu4-oracle-epochs/v1",
        "epochs": [{"token": epoch.token, "scenario": epoch.scenario_token, "mode": epoch.mode,
                    "capture_enabled": epoch.capture_enabled} for epoch in sequence],
        "records": len(records),
        "per_epoch": counts,
    }
