import gc
import os
import shutil
import tempfile
import unittest
import warnings

import rocksdb


def make_test_provider():
    # The CTR/ROT13 test provider warns by design; silence it for tests that
    # are not about the warning itself.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return rocksdb.EncryptionProvider("CTR://test")


class TestEncryptionProvider(unittest.TestCase):
    def test_create_from_string(self):
        provider = make_test_provider()
        self.assertEqual(provider.id, "CTR")

    def test_nested_cipher_spec(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            provider = rocksdb.EncryptionProvider("id=CTR;cipher=ROT13")
        self.assertEqual(provider.id, "CTR")

    def test_bytes_spec(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            provider = rocksdb.EncryptionProvider(b"CTR://test")
        self.assertEqual(provider.id, "CTR")

    def test_ctr_provider_warns(self):
        with self.assertWarns(UserWarning):
            rocksdb.EncryptionProvider("CTR://test")

    def test_unknown_provider_raises(self):
        with self.assertRaises(rocksdb.errors.NotSupported):
            rocksdb.EncryptionProvider("NoSuchProvider")

    def test_empty_spec_raises(self):
        with self.assertRaises(rocksdb.errors.InvalidArgument):
            rocksdb.EncryptionProvider("")

    def test_bare_ctr_raises_notfound(self):
        # "CTR" without a cipher is incomplete: rocksdb reports NotFound
        # ("Missing configurable object: cipher").
        with self.assertRaises(rocksdb.errors.NotFound):
            rocksdb.EncryptionProvider("CTR")

    def test_spec_type_error(self):
        with self.assertRaises(TypeError):
            rocksdb.EncryptionProvider(42)

    def test_add_cipher_passthrough(self):
        # A complete CTR provider rejects extra keys; this proves the call
        # reaches rocksdb and the Status maps to our exception hierarchy.
        provider = make_test_provider()
        with self.assertRaises(rocksdb.errors.NotSupported):
            provider.add_cipher("ROT13", b"", for_write=True)
        with self.assertRaises(rocksdb.errors.NotSupported):
            provider.add_cipher("", b"0123456789abcdef", for_write=False)

    def test_add_cipher_key_type_error(self):
        provider = make_test_provider()
        with self.assertRaises(TypeError):
            provider.add_cipher("ROT13", "not-bytes")


if __name__ == '__main__':
    unittest.main()
