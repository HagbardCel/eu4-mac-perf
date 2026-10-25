"""Portable sealed-session Tier 1 certifier.

The public entry point is ``certify_session``. It reads one sealed PR D session
and one frozen candidate, replays every eligible fixture, and writes a
certificate outside the evidence directory.

The exported object is a callable instance rather than a bare function so that
storing it on a class (as the E2 acceptance suite does) does not bind the test
instance in as the session directory.
"""
from __future__ import annotations

from functools import update_wrapper

from oracle.certify.certify import certify_session as _certify_session
from oracle.certify.errors import CertifyError


class _UnboundCertifier:
    """Call ``certify_session`` without becoming a bound method."""

    def __call__(self, session_dir, descriptor, policy, candidate, *,
                 certificate_dir):
        return _certify_session(
            session_dir, descriptor, policy, candidate,
            certificate_dir=certificate_dir)


certify_session = update_wrapper(_UnboundCertifier(), _certify_session)

__all__ = ["CertifyError", "certify_session"]
