"""Checked-in authority registry for original-API control renderers.

Adding an entry is a reviewed code change. The qualification gate refuses Metal
blame for any renderer whose sha256 is absent here; a caller-supplied authority
file is not a pin and is not accepted.

An entry binds, in one reviewed object:

``name``
    Stable renderer identity that the signed receipt must echo.
``api``
    Must be ``"opengl"``.  A Metal replay is not an original-API control.
``renderer_sha256``
    The registry key: the digest of the exact executable the qualifier runs.
``public_key``
    Ed25519 public key (32 bytes, hex) whose private half signs the execution
    receipt.  The receipt authenticates execution identity; it is not proof of
    OpenGL calls, which follows from the reviewed implementation and the
    digest-pinned execution path.
``components``
    Additional reviewed pieces of the execution path (for example the native
    original-GL executor launched by the reviewed Python driver), each a
    ``{"role", "sha256"}`` pair.  The receipt must enumerate exactly these
    digests; a renderer cannot silently swap a component.
``qualified`` / ``qualified_by``
    Explicit review state and the identity that reviewed the exact head.

A receipt is self-consistency only.  Before adding an entry, the receipt must be
signed by the key registered here and must bind the renderer digest, component
digests, challenge nonce, capture/session/stream identity, selected draw and
checkpoint, effective input-package digest, output image digest/dimensions/
format and completed execution status.

Population condition: the renderer must be implemented, independently
reviewed, pinned here and verified against its own synthetic original-GL
reference before the first entry.  This file intentionally ships empty.
"""

from __future__ import annotations

SCHEMA = "gfx-gl-control-renderer-authorities/v2"

# sha256 -> {"name", "api", "qualified", "qualified_by", "public_key",
#            "components": [{"role", "sha256"}], "source"}
# Intentionally empty: no original-API control renderer has been reviewed and
# pinned at this head. The 904 control and any ad-hoc renderer therefore remain
# unqualified for causal Metal attribution.
AUTHORITIES: dict[str, dict] = {}
