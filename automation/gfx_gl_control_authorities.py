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
# from renderer/gl/eu4_gfx_draw_control.c with the deterministic recipe:
#   clang -O2 -g0 -w -Wl,-no_uuid \
#       -o <dir>/eu4_gfx_draw_control renderer/gl/eu4_gfx_draw_control.c \
#       -framework OpenGL
# The output basename must be eu4_gfx_draw_control; clang embeds it, so a
# different -o basename changes the digest even with identical source.  With
# this recipe the digest is independent of the build directory.  The binary
# digest below is that reviewed build; the public key half lives in
# .local/m7/authority/gl-control-ed25519.key (not checked in).  A re-review is
# required before changing any digest or adding another renderer.
AUTHORITIES: dict[str, dict] = {
    "19f819b444738888b84ddbbb68e413af0e908ba35c738db5811d689257b8b70b": {
        "name": "m7-original-gl-control",
        "api": "opengl",
        "qualified": True,
        "qualified_by": (
            "independent review of exact head 283c3e12: review-fixes and "
            "review-integrated (DSH subagents), both APPROVE; see "
            "analysis/m7/final-review-279.md"),
        "public_key": (
            "b129e1bcd834600e8a3986159b7d9a540d1951f09f30ce3bfd4881844247a727"),
        "components": [
            {"role": "native-gl-executor",
             "sha256": "254618ef8ab232fd1b3c42e6186d642702e714e70d79c92f6d2d908cfa3abc7a"},
            {"role": "replay-input-verifier",
             "sha256": "6c93974f4df9612c45c0645ad27a0d6cd5b3a7885ea2b6829be43b5401c02166"},
            {"role": "receipt-signer",
             "sha256": "52e03dd0c0b80c10c4e8acf2f72d4307a07c2c0321a06bd1107e0903f3ffa3b4"},
        ],
        "source": (
            "renderer/gl/eu4_gfx_draw_control.c + "
            "automation/gfx_gl_control_renderer.py"),
    },
}
