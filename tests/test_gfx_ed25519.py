"""RFC 8032 Ed25519 vectors and receipt-signature contract properties."""

from __future__ import annotations

import unittest

from automation import gfx_ed25519


# RFC 8032 §7.1, TEST 1-3 (secret seed, public key, message, signature).
_VECTORS = (
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
)


class Ed25519Tests(unittest.TestCase):
    def test_rfc_8032_vectors_sign_and_verify(self):
        for secret, public, message, signature in _VECTORS:
            with self.subTest(public=public[:16]):
                seed = bytes.fromhex(secret)
                body = bytes.fromhex(message)
                expected_public = bytes.fromhex(public)
                expected_signature = bytes.fromhex(signature)
                self.assertEqual(
                    gfx_ed25519.derive_public_key(seed), expected_public)
                self.assertEqual(
                    gfx_ed25519.sign(seed, body), expected_signature)
                self.assertTrue(gfx_ed25519.verify(
                    expected_public, body, expected_signature))

    def test_a_different_message_or_signature_is_refused(self):
        secret, public, message, signature = _VECTORS[0]
        seed = bytes.fromhex(secret)
        public_key = bytes.fromhex(public)
        body = bytes.fromhex(message)
        receipt_signature = gfx_ed25519.sign(seed, body)
        self.assertFalse(gfx_ed25519.verify(
            public_key, body + b"x", receipt_signature))
        altered = bytearray(receipt_signature)
        altered[0] ^= 1
        self.assertFalse(gfx_ed25519.verify(public_key, body, bytes(altered)))

    def test_a_different_key_is_refused(self):
        _secret_a, public_b, message, _signature = _VECTORS[1]
        seed = bytes.fromhex(_VECTORS[0][0])
        body = bytes.fromhex(message)
        signature = gfx_ed25519.sign(seed, body)
        self.assertFalse(gfx_ed25519.verify(
            bytes.fromhex(public_b), body, signature))

    def test_malformed_keys_and_signatures_are_refused_not_raised(self):
        seed = bytes.fromhex(_VECTORS[0][0])
        public = bytes.fromhex(_VECTORS[0][1])
        signature = gfx_ed25519.sign(seed, b"payload")
        for bad_public, bad_signature in (
                (b"", signature),
                (public[:-1], signature),
                (public, b""),
                (public, signature[:-1]),
                (b"\x00" * 32, signature),
                (public, b"\xff" * 64)):
            with self.subTest(public=len(bad_public),
                              signature=len(bad_signature)):
                self.assertFalse(gfx_ed25519.verify(
                    bad_public, b"payload", bad_signature))

    def test_off_curve_and_non_canonical_points_are_refused(self):
        seed = bytes.fromhex(_VECTORS[0][0])
        public = bytes.fromhex(_VECTORS[0][1])
        signature = gfx_ed25519.sign(seed, b"payload")
        # y=2 has no square root of x^2; y=p is not a canonical field element.
        off_curve = (2).to_bytes(32, "little")
        non_canonical = (gfx_ed25519.Q).to_bytes(32, "little")
        self.assertFalse(gfx_ed25519.verify(off_curve, b"payload", signature))
        self.assertFalse(gfx_ed25519.verify(
            non_canonical, b"payload", signature))
        # A non-canonical R (y=p) must also fail even with a valid key.
        non_canonical_r = non_canonical + signature[32:]
        self.assertFalse(gfx_ed25519.verify(public, b"payload",
                                            non_canonical_r))

    def test_decode_rejects_off_curve_and_non_canonical_points_directly(self):
        # These assertions discriminate the decode checks themselves; driving
        # them only through verify() can be masked by the later equation.
        for encoded in ((2).to_bytes(32, "little"),
                        gfx_ed25519.Q.to_bytes(32, "little"),
                        (gfx_ed25519.Q + 1).to_bytes(32, "little")):
            with self.subTest(y=encoded.hex()[:8]):
                with self.assertRaises(ValueError):
                    gfx_ed25519._decode(encoded)

    def test_small_order_points_cannot_forge_a_receipt(self):
        # The classic identity-key forgery: A = identity, R = identity, S = 0
        # satisfies the cofactorless equation for *every* message unless the
        # verifier rejects small-order points.
        identity = bytes.fromhex(
            "0100000000000000000000000000000000000000000000000000000000000000")
        order_two = bytes.fromhex(
            "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f")
        order_four = bytes(32)
        forgery = identity + bytes(32)
        for point in (identity, order_two, order_four):
            with self.subTest(point=point.hex()[:8]):
                self.assertFalse(gfx_ed25519.verify(
                    point, b"arbitrary receipt", forgery))
        # A valid key and signature with a small-order R is refused too.
        seed = bytes.fromhex(_VECTORS[0][0])
        public = bytes.fromhex(_VECTORS[0][1])
        signature = gfx_ed25519.sign(seed, b"payload")
        self.assertFalse(gfx_ed25519.verify(
            public, b"payload", order_two + signature[32:]))

    def test_invalid_scalar_encodings_are_refused(self):
        seed = bytes.fromhex(_VECTORS[0][0])
        public = bytes.fromhex(_VECTORS[0][1])
        signature = gfx_ed25519.sign(seed, b"payload")
        encoded_r = signature[:32]
        original = int.from_bytes(signature[32:], "little")
        for scalar in (gfx_ed25519.L, original + gfx_ed25519.L,
                       gfx_ed25519.L + 1, (1 << 256) - 1):
            with self.subTest(scalar=scalar):
                forged = encoded_r + scalar.to_bytes(32, "little")
                self.assertFalse(gfx_ed25519.verify(public, b"payload",
                                                    forged))

    def test_a_receipt_like_body_round_trips(self):
        seed = bytes(range(32))
        public = gfx_ed25519.derive_public_key(seed)
        body = b'{"nonce":"00","execution":"completed"}'
        signature = gfx_ed25519.sign(seed, body)
        self.assertTrue(gfx_ed25519.verify(public, body, signature))
        # Canonical bodies are what the qualifier signs; one altered byte of
        # JSON invalidates the receipt.
        self.assertFalse(gfx_ed25519.verify(
            public, body.replace(b"completed", b"failed  "), signature))


if __name__ == "__main__":
    unittest.main()
