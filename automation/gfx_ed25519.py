"""Dependency-free Ed25519 signatures for execution receipts.

The qualification gate must verify that a receipt was produced by the reviewed
renderer that executed, not hand-written by the caller.  A receipt is signed by
the renderer with a private key whose public half is registered in
:mod:`automation.gfx_gl_control_authorities`; the qualifier verifies that
signature before it reads the pixels.

This module implements only RFC 8032 Ed25519 over a 32-byte seed and a 32-byte
public key.  It is deliberately small and dependency-free so an offline,
checked-out tree can verify a receipt without installing a crypto package, and
so the renderer and the verifier share one canonical implementation.

:func:`verify` is stricter than bare RFC 8032 decoding at the decision
boundary: it rejects non-canonical encodings, points that are not on the curve,
non-canonical scalars and small-order public keys or R values.  The last check
closes the trivial identity-key forgery.  The scalar multiplication is not
constant time; it protects a local execution receipt, not a remote signing
oracle, and the private key is used only by the reviewed renderer.

The signature authenticates *execution identity*: the holder of the registered
private key ran under the reviewed renderer digest.  It is not, by itself,
evidence that the renderer made OpenGL calls.  That property follows from the
reviewed implementation and the pinned component digests; see
``gfx_gl_control_authorities``.
"""

from __future__ import annotations

import hashlib

Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, Q - 2, Q)) % Q
I = pow(2, (Q - 1) // 4, Q)

_POINT_IDENTITY = (0, 1, 1, 0)
_BY = (4 * pow(5, Q - 2, Q)) % Q


def _x_recover(y: int) -> int:
    xx = ((y * y - 1) * pow(D * y * y + 1, Q - 2, Q)) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = (x * I) % Q
    if (x * x - xx) % Q != 0:
        raise ValueError("ed25519 point is not on the curve")
    if x % 2 != 0:
        x = Q - x
    return x


_BX = _x_recover(_BY)
_BASE = (_BX, _BY, 1, (_BX * _BY) % Q)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % Q
    b = ((y1 + x1) * (y2 + x2)) % Q
    c = (t1 * 2 * D * t2) % Q
    d = (z1 * 2 * z2) % Q
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _double(p):
    return _add(p, p)


def _scalar_mult(point, scalar: int):
    result = _POINT_IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _double(addend)
        scalar >>= 1
    return result


def _encode(point) -> bytes:
    x, y, z, _t = point
    inverse = pow(z, Q - 2, Q)
    x = (x * inverse) % Q
    y = (y * inverse) % Q
    encoded = bytearray(y.to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


_ENCODED_IDENTITY = _encode(_POINT_IDENTITY)


def _has_small_order(point) -> bool:
    """True when ``[8]P`` is the identity, i.e. P has order dividing eight.

    RFC 8032 decoding alone accepts these points (the identity, and the
    order-2/4/8 points).  Verifying against a small-order public key admits a
    trivial forgery (identity key, identity R, S=0 verifies every message), so
    a decision-boundary verifier refuses them, as libsodium does.
    """
    return _encode(_scalar_mult(point, 8)) == _ENCODED_IDENTITY


def _decode(encoded: bytes):
    if not isinstance(encoded, (bytes, bytearray)) or len(encoded) != 32:
        raise ValueError("ed25519 point must be 32 bytes")
    raw = bytearray(encoded)
    sign = raw[31] >> 7
    raw[31] &= 0x7F
    y = int.from_bytes(raw, "little")
    if y >= Q:
        raise ValueError("ed25519 point is not canonical")
    x = _x_recover(y)
    if (x & 1) != sign:
        x = Q - x
    point = (x, y, 1, (x * y) % Q)
    if _encode(point) != bytes(encoded):
        raise ValueError("ed25519 point is not on the curve")
    return point


def _clamp(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def derive_public_key(seed: bytes) -> bytes:
    """Return the 32-byte Ed25519 public key for a 32-byte private seed."""
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, _prefix = _clamp(bytes(seed))
    return _encode(_scalar_mult(_BASE, scalar))


def sign(seed: bytes, message: bytes) -> bytes:
    """Return the 64-byte RFC 8032 signature of *message*."""
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    seed = bytes(seed)
    message = bytes(message)
    scalar, prefix = _clamp(seed)
    public = _encode(_scalar_mult(_BASE, scalar))
    nonce = int.from_bytes(
        hashlib.sha512(prefix + message).digest(), "little") % L
    encoded_r = _encode(_scalar_mult(_BASE, nonce))
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public + message).digest(), "little") % L
    return encoded_r + ((nonce + challenge * scalar) % L).to_bytes(32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return True only for a valid signature over *message*.

    Every malformed input is False: a verifier at a decision boundary must not
    raise into its caller.
    """
    try:
        if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
            return False
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
            return False
        public_key = bytes(public_key)
        signature = bytes(signature)
        message = bytes(message)
        point_a = _decode(public_key)
        encoded_r = signature[:32]
        point_r = _decode(encoded_r)
        scalar_s = int.from_bytes(signature[32:], "little")
        if scalar_s >= L:
            return False
        # A small-order public key or R admits trivial forgeries; a registered
        # authority key is generated normally, so refusing is safe and narrow.
        if _has_small_order(point_a) or _has_small_order(point_r):
            return False
        challenge = int.from_bytes(
            hashlib.sha512(encoded_r + public_key + message).digest(),
            "little") % L
        expected = _add(point_r, _scalar_mult(point_a, challenge))
        actual = _scalar_mult(_BASE, scalar_s)
        return _encode(expected) == _encode(actual)
    except (ValueError, TypeError, OverflowError):
        return False
