"""
Unit Tests for LINE Signature Verification (F29 Phase 3).

Updated for S32: tests now exercise the shared ``verify_hmac_signature``
primitive with ``digest_encoding='base64'`` instead of the removed inline
``LINEWebhookServer._verify_signature`` method.
"""

import base64
import hashlib
import hmac
import unittest

from connector.security_profile import AuthScheme, verify_hmac_signature


class TestLINESignature(unittest.TestCase):
    def test_verify_valid_signature(self):
        body = b'{"events":[]}'
        secret = "mysecret"
        sig = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")

        result = verify_hmac_signature(
            body,
            signature_header=sig,
            secret=secret,
            algorithm="sha256",
            digest_encoding="base64",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.scheme, AuthScheme.HMAC_SHA256.value)

    def test_verify_invalid_signature(self):
        body = b'{"events":[]}'
        result = verify_hmac_signature(
            body,
            signature_header="invalid_sig",
            secret="mysecret",
            digest_encoding="base64",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "signature_mismatch")

    def test_verify_empty(self):
        result = verify_hmac_signature(
            b"",
            signature_header="",
            secret="mysecret",
            digest_encoding="base64",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_signature_header")


if __name__ == "__main__":
    unittest.main()
