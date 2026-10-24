"""Independent E1 acceptance contract (E2-owned).

Implementation agents must not edit this package. Assertions treat
``oracle.certify.certify_session`` as a black box.
"""

from .contract import (
    CERTIFICATE_SCHEMA,
    assert_certificate_shape,
    assert_evidence_not_live_grounded,
    assert_promotion,
    assert_rejects_false_certification,
    assert_trusted_build_required,
    false_certifier_stand_in,
    self_declared_build_stand_in,
)

__all__ = [
    "CERTIFICATE_SCHEMA",
    "assert_certificate_shape",
    "assert_evidence_not_live_grounded",
    "assert_promotion",
    "assert_rejects_false_certification",
    "assert_trusted_build_required",
    "false_certifier_stand_in",
    "self_declared_build_stand_in",
]
