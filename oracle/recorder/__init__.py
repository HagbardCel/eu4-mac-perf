"""Oracle P4 recorder: versioned observation records and an immutable evidence store.

Offline only. Nothing in this package launches EU IV, opens a game process, or
attaches to a binary; the recorder consumes already-captured native events. P4.1
defines the record schema, its canonicalization, and the store that seals it.
"""
from oracle.recorder.schema import (
    CANONICALIZATION_VERSION,
    CAPTURE_STATES,
    FIDELITY_MODES,
    FIELD_CONFIDENCE,
    RECORD_SCHEMA_VERSION,
    SchemaError,
    canonical_bytes,
    qualifies_for_semantic,
    record_digest,
    seal,
    semantic_digest,
    semantic_projection,
    validate_payload,
    verify,
)
from oracle.recorder.store import EVIDENCE_MANIFEST_VERSION, EvidenceStore, StoreError

__all__ = [
    "CANONICALIZATION_VERSION",
    "CAPTURE_STATES",
    "EVIDENCE_MANIFEST_VERSION",
    "EvidenceStore",
    "FIDELITY_MODES",
    "FIELD_CONFIDENCE",
    "RECORD_SCHEMA_VERSION",
    "SchemaError",
    "StoreError",
    "canonical_bytes",
    "qualifies_for_semantic",
    "record_digest",
    "seal",
    "semantic_digest",
    "semantic_projection",
    "validate_payload",
    "verify",
]
