import os
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
import database


class SecretStorageTests(unittest.TestCase):
    def test_missing_key_cannot_store_plaintext_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "before storing"):
                database.encrypt_secret("test-only-provider-token")
            self.assertEqual(database.encrypt_secret(""), "")

    def test_round_trip_and_missing_or_wrong_key_fail_closed(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"NEXORA_ENCRYPTION_KEY": key}):
            ciphertext = database.encrypt_secret("test-only-provider-token")
            self.assertNotIn("test-only-provider-token", ciphertext)
            self.assertEqual(database.decrypt_secret(ciphertext), "test-only-provider-token")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                database.decrypt_secret(ciphertext)
        with patch.dict(os.environ, {"NEXORA_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(RuntimeError):
                database.decrypt_secret(ciphertext)

    def test_legacy_plaintext_remains_readable_for_migration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(database.decrypt_secret("test-only-legacy-token"), "test-only-legacy-token")
