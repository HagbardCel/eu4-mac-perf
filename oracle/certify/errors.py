"""Errors raised by the E1 Tier 1 certifier."""


class CertifyError(ValueError):
    """The certifier was invoked with unusable inputs.

    Evidence problems do not raise: they fail closed into an ineligible session
    or a failed certificate. This error is reserved for caller mistakes such as a
    missing candidate path or a policy file that is not a registered policy.
    """
