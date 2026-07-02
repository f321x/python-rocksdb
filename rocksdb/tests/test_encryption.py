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


def make_test_env():
    return rocksdb.EncryptedEnv(make_test_provider())


class EncryptedDBHelper(unittest.TestCase):
    def setUp(self):
        self.loc = tempfile.mkdtemp()
        self._dbs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for db in self._dbs:
            db.close()
        self._dbs = []
        gc.collect()
        if os.path.exists(self.loc):
            shutil.rmtree(self.loc)

    def _open(self, name="db", env=None, cls=rocksdb.DB, **extra):
        opts = rocksdb.Options(create_if_missing=True)
        if env is not None:
            opts.env = env
        if cls is rocksdb.TransactionDB:
            extra.setdefault("tdb_opts", rocksdb.TransactionDBOptions())
        db = cls(os.path.join(self.loc, name), opts, **extra)
        self._dbs.append(db)
        return db


class TestEncryptedDB(EncryptedDBHelper):
    MARKER = b"very-secret-plaintext-marker-0123456789"

    def test_roundtrip_and_reopen(self):
        env = make_test_env()
        db = self._open(env=env)
        db.put(b"key", self.MARKER)
        self.assertEqual(db.get(b"key"), self.MARKER)
        db.close()
        db2 = self._open(env=env)
        self.assertEqual(db2.get(b"key"), self.MARKER)

    def test_iteration(self):
        env = make_test_env()
        db = self._open(env=env)
        for i in range(100):
            db.put(b"key%03d" % i, b"value%03d" % i)
        it = db.iteritems()
        it.seek_to_first()
        items = list(it)
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0], (b"key000", b"value000"))

    def _files_containing(self, root, needle):
        hits = []
        for dirpath, _, files in os.walk(root):
            for fname in files:
                with open(os.path.join(dirpath, fname), "rb") as f:
                    if needle in f.read():
                        hits.append(fname)
        return hits

    def test_plaintext_not_on_disk(self):
        env = make_test_env()
        db = self._open(name="enc", env=env)
        db.put(b"key", self.MARKER)      # lands at least in the WAL
        db.close()
        self.assertEqual(
            self._files_containing(os.path.join(self.loc, "enc"),
                                   self.MARKER),
            [])
        current = os.path.join(self.loc, "enc", "CURRENT")
        with open(current, "rb") as f:
            self.assertFalse(f.read().startswith(b"MANIFEST-"))
        # control: without the env the marker IS on disk
        db2 = self._open(name="plain")
        db2.put(b"key", self.MARKER)
        db2.close()
        self.assertNotEqual(
            self._files_containing(os.path.join(self.loc, "plain"),
                                   self.MARKER),
            [])

    def test_open_without_env_fails(self):
        env = make_test_env()
        db = self._open(name="enc2", env=env)
        db.put(b"key", b"value")
        db.close()
        with self.assertRaises(rocksdb.errors.Error):
            self._open(name="enc2")

    def test_env_outlives_dropped_references(self):
        env = make_test_env()
        opts = rocksdb.Options(create_if_missing=True, env=env)
        db = rocksdb.DB(os.path.join(self.loc, "gcdb"), opts)
        self._dbs.append(db)
        del env, opts
        gc.collect()
        db.put(b"key", b"value")         # env must still be alive
        self.assertEqual(db.get(b"key"), b"value")
        db.close()

    def test_env_reassignment_while_in_use_is_refused(self):
        # rocksdb copies the raw env pointer at Open; swapping Options.env
        # while the Options are claimed by an open DB would let the DB run
        # on an env it never pinned (use-after-free once that env's wrapper
        # is collected), so the setter refuses.
        env = make_test_env()
        opts = rocksdb.Options(create_if_missing=True, env=env)
        db = rocksdb.DB(os.path.join(self.loc, "pin"), opts)
        self._dbs.append(db)
        with self.assertRaises(rocksdb.errors.InvalidArgument):
            opts.env = make_test_env()
        with self.assertRaises(rocksdb.errors.InvalidArgument):
            opts.env = None
        self.assertIs(opts.env, env)    # unchanged
        db.put(b"key", b"value")
        self.assertEqual(db.get(b"key"), b"value")
        db.close()
        # After close() the claim is released: reassignment is allowed, and
        # the closed DB's pin (DB.py_env) keeps the old env safely alive.
        opts.env = make_test_env()
        del env
        gc.collect()
        db2 = rocksdb.DB(os.path.join(self.loc, "pin2"), opts)
        self._dbs.append(db2)
        db2.put(b"key", b"value")
        self.assertEqual(db2.get(b"key"), b"value")

    def test_env_shared_by_sequential_dbs(self):
        env = make_test_env()
        for name in (b"one", b"two"):
            db = self._open(name=name.decode(), env=env)
            db.put(b"key", name)
            db.close()
        db = self._open(name="one", env=env)
        self.assertEqual(db.get(b"key"), b"one")

    def test_transaction_db(self):
        env = make_test_env()
        db = self._open(name="txn", env=env, cls=rocksdb.TransactionDB)
        db.put(b"key", b"value")
        self.assertEqual(db.get(b"key"), b"value")


class TestEncryptedBackup(EncryptedDBHelper):
    def test_backup_and_restore(self):
        env = make_test_env()
        db = self._open(name="src", env=env)
        db.put(b"key", b"value")
        backup_dir = os.path.join(self.loc, "backups")
        engine = rocksdb.BackupEngine(backup_dir, env=env)
        engine.create_backup(db, flush_before_backup=True)
        db.close()
        restore_loc = os.path.join(self.loc, "restored")
        engine.restore_latest_backup(restore_loc, restore_loc)
        del engine
        gc.collect()
        restored = self._open(name="restored", env=env)
        self.assertEqual(restored.get(b"key"), b"value")

    def test_env_type_error(self):
        with self.assertRaises(TypeError):
            rocksdb.BackupEngine(os.path.join(self.loc, "b"), env="nope")

    def test_engine_in_reference_cycle(self):
        # A subclass instance in a reference cycle is destroyed by cyclic
        # GC; BackupEngine is no_gc_clear so the engine is deleted before
        # the env reference is dropped (otherwise the C++ env would be
        # freed while the engine still uses it).
        class MyEngine(rocksdb.BackupEngine):
            pass

        env = make_test_env()
        db = self._open(name="cyc", env=env)
        db.put(b"key", b"value")
        engine = MyEngine(os.path.join(self.loc, "cyc_backups"), env=env)
        engine.create_backup(db, flush_before_backup=True)
        engine.self_ref = engine        # the cycle
        del engine, env
        gc.collect()
        db.put(b"key2", b"value2")      # db's own env still fine
        self.assertEqual(db.get(b"key2"), b"value2")


if __name__ == '__main__':
    unittest.main()
