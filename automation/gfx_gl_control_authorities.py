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
    "77d152c5d9e5822a852be746621ed930f04af02aa538ecaa9fd1fbcb2faf1af6": {
        "name": "m7-original-gl-control",
        "api": "opengl",
        "qualified": True,
        "qualified_by": (
            "independent review review-pr3 (DSH subagent), exact PR3 head "
            "93a7c9110c15be49cac922ac5f261753e2bed005"),
        "public_key": (
            "b129e1bcd834600e8a3986159b7d9a540d1951f09f30ce3bfd4881844247a727"),
        "components": [
            {"role": "native-gl-executor",
             "sha256": "d03228553f3f31f30e92a5cde588f879220a71b912826a1a12c8a9292d74164b"},
            {"role": "replay-input-verifier",
             "sha256": "831050f051a3b8097431d71fafe96775878422b6c268483360a3eaa17d179526"},
            {"role": "receipt-signer",
             "sha256": "a72d7fccc5430be1192ea010bc5e432266341dafc82bc4c33941445ea54da8ce"},
        ],
        "source": (
            "benchmark/eu4_gfx_draw_control.c + "
            "automation/gfx_gl_control_renderer.py"),
    },
}
