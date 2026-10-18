"""SDK real y JWT firmados localmente; se simula sólo la descarga de certificados."""

import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import firebase_admin
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials

from config import Settings
from main import app


class TestCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


class SignedTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Unit test only")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(cls.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .sign(cls.key, hashes.SHA256()))
        cls.certificate = cert.public_bytes(serialization.Encoding.PEM).decode()

    def setUp(self):
        self.application = firebase_admin.initialize_app(
            TestCredential(), {"projectId": "unit-test-project"}, name="unit-" + str(uuid4()),
        )
        self.client = TestClient(app)
        self.patches = [
            patch("services.auth.get_firebase_app", return_value=self.application),
            patch("services.auth.get_settings", return_value=Settings(firebase_check_revoked=False)),
            patch("google.oauth2.id_token._fetch_certs", return_value={"unit-key": self.certificate}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        firebase_admin.delete_app(self.application)

    def token(self, key=None, **changes):
        now = int(time.time())
        claims = dict(iss="https://securetoken.google.com/unit-test-project",
                      aud="unit-test-project", sub="verified-user", iat=now,
                      exp=now + 3600, auth_time=now)
        claims.update(changes)
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "unit-key"})

    def test_real_sdk_accepts_signed_id_token(self):
        response = self.client.get("/auth/me", headers={"Authorization": "Bearer " + self.token()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"uid": "verified-user"})

    def test_real_sdk_rejects_expired_wrong_audience_issuer_signature_and_malformed_tokens(self):
        tokens = [self.token(exp=int(time.time()) - 100), self.token(aud="other-project"),
                  self.token(iss="https://attacker.example"), self.token(key=self.other_key), "not.a.jwt"]
        for token in tokens:
            with self.subTest(token_kind=tokens.index(token)):
                response = self.client.get("/auth/me", headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 401)
