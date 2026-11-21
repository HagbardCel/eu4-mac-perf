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
reference before the first entry.  This file pins exactly the reviewed M7 original-OpenGL control; adding or
changing an entry is a separately reviewed code change.
"""

from __future__ import annotations

SCHEMA = "gfx-gl-control-renderer-authorities/v2"

# sha256 -> {"name", "api", "qualified", "qualified_by", "public_key",
#            "components": [{"role", "sha256"}], "source"}
#
# The first entry pins the M7 original-OpenGL selected-draw control.  Its
# driver is automation/gfx_gl_control_renderer.py; the native executor is built
# from benchmark/eu4_gfx_draw_control.c with:
#   clang -O2 -w -I benchmark -o .local/m7/authority/eu4_gfx_draw_control \
#       benchmark/eu4_gfx_draw_control.c -framework OpenGL
# The binary digest below is that reviewed build; the public key half lives in
# .local/m7/authority/gl-control-ed25519.key (not checked in).  A re-review is
# required before changing any digest or adding another renderer.
AUTHORITIES: dict[str, dict] = {
    "b05ab20a307b8281144b10a32c92b143a9e4ecccb2a47d94cc67e4a2f6114c74": {
        "name": "m7-original-gl-control",
        "api": "opengl",
        "qualified": True,
        "qualified_by": (
            "independent review of the post-fix #279 integration head "
            "(review-final); the earlier PR3 review "
            "93a7c9110c15be49cac922ac5f261753e2bed005 and the capacity review "
            "at 92f84b4137f15fb8ded80f2b763d28b064dea010 cover the "
            "pre-fix implementation"),
        "public_key": (
            "b129e1bcd834600e8a3986159b7d9a540d1951f09f30ce3bfd4881844247a727"),
        "components": [
            {"role": "native-gl-executor",
             "sha256": "285d1fb4ba936e24ae5a247b572098e3ebbdf1ffa8836afb2a585e78b07ab878"},
            {"role": "replay-input-verifier",
             "sha256": "640994da46e11d145da27766b78637be41ad3d556c759a334ecc2060b6779119"},
            {"role": "receipt-signer",
             "sha256": "52e03dd0c0b80c10c4e8acf2f72d4307a07c2c0321a06bd1107e0903f3ffa3b4"},
        ],
        "source": (
            "benchmark/eu4_gfx_draw_control.c + "
            "automation/gfx_gl_control_renderer.py"),
    },
}
