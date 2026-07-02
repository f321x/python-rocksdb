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


class TestEncryptedEnv(unittest.TestCase):
    def test_env_is_abstract(self):
        with self.assertRaises(TypeError):
            rocksdb.Env()

    def test_from_provider(self):
        provider = make_test_provider()
        env = rocksdb.EncryptedEnv(provider)
        self.assertIs(env.provider, provider)
        self.assertIsInstance(env, rocksdb.Env)

    def test_from_spec_string(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            env = rocksdb.EncryptedEnv("CTR://test")
        self.assertEqual(env.provider.id, "CTR")

    def test_provider_type_error(self):
        with self.assertRaises(TypeError):
            rocksdb.EncryptedEnv(42)

    def test_provider_readonly(self):
        env = rocksdb.EncryptedEnv(make_test_provider())
        with self.assertRaises(AttributeError):
            env.provider = make_test_provider()

    def test_provider_shared_by_envs(self):
        provider = make_test_provider()
        env1 = rocksdb.EncryptedEnv(provider)
        env2 = rocksdb.EncryptedEnv(provider)
        self.assertIs(env1.provider, env2.provider)


class TestOptionsEnv(unittest.TestCase):
    def test_default_is_none(self):
        self.assertIsNone(rocksdb.Options().env)

    def test_set_get_reset(self):
        env = rocksdb.EncryptedEnv(make_test_provider())
        opts = rocksdb.Options()
        opts.env = env
        self.assertIs(opts.env, env)
        opts.env = None
        self.assertIsNone(opts.env)

    def test_set_via_kwargs(self):
        env = rocksdb.EncryptedEnv(make_test_provider())
        opts = rocksdb.Options(create_if_missing=True, env=env)
        self.assertIs(opts.env, env)

    def test_type_error(self):
        opts = rocksdb.Options()
        with self.assertRaises(TypeError):
            opts.env = "not an env"


if __name__ == '__main__':
    unittest.main()
