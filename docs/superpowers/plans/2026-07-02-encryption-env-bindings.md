# Encryption-at-rest framework bindings — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose RocksDB's encryption framework (`EncryptionProvider`, `NewEncryptedEnv`, `Options.env`, `BackupEngine(env=...)`) through the Cython binding, with the code path CI-tested via RocksDB's built-in CTR/ROT13 test provider.

**Architecture:** One new declaration file (`rocksdb/env_encryption.pxd`) transcribing the public header; new `EncryptionProvider` / `Env` / `EncryptedEnv` cdef classes plus an `Options.env` property in `rocksdb/_rocksdb.pyx`, following the existing keep-alive pattern (`py_comparator` et al.); `DB` pins the env reference at open so the env provably outlives the DB (free-threading-safe teardown ordering). No setup.py changes (no new link deps); no CI workflow changes (tests land in `rocksdb/tests/`, already run by `pytest --pyargs rocksdb`).

**Tech Stack:** Cython 3.2 (`freethreading_compatible`), RocksDB 8.x–10.x public API, unittest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-02-encryption-env-bindings-design.md` — read it first.
- The binding ships NO cryptography; only the framework passthrough.
- Provider loading uses strict `ConfigOptions` (`ignore_unsupported_options = False`) AND raises `InvalidArgument` when the returned `shared_ptr` is null despite Status OK (empty-spec case).
- A provider with `IsInstanceOf("CTR")` true emits a `UserWarning` (never refuse).
- Rebuild command after any `.pyx`/`.pxd` edit: `python3 setup.py build_ext --inplace` (Cython 3.2.6 present; Python 3.13). Tests can then run in-tree: `python3 -m pytest rocksdb/tests/test_encryption.py -v` (the in-tree `.so` makes plain pytest work per CLAUDE.md).
- Empirically verified behaviors the tests encode (probed against librocksdb 9.10):
  - `CreateFromString("CTR://test")` → OK, `GetId()=="CTR"`, prefix 4096
  - `CreateFromString("id=CTR;cipher=ROT13")` → OK, complete provider
  - `CreateFromString("CTR")` → `NotFound: Missing configurable object: cipher`
  - `CreateFromString("NoSuchProvider")` strict → `NotSupported`
  - `CreateFromString("")` strict → OK + null result
  - `AddCipher` on a complete CTR provider → `NotSupported: Cannot add keys to CTREncryptionProvider`
  - Opening an encrypted DB without the env → `Corruption`
- Commit after each green task on branch `encryption-env`. Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `EncryptionProvider` class + declarations

**Files:**
- Create: `rocksdb/env_encryption.pxd`
- Modify: `rocksdb/_rocksdb.pyx` (imports; new class before `cdef class ColumnFamilyOptions`)
- Test: `rocksdb/tests/test_encryption.py` (new)

**Interfaces:**
- Consumes: existing `check_status`, `bytes_to_string`, error classes, `from .std_memory cimport shared_ptr`.
- Produces: `rocksdb.EncryptionProvider(spec: str|bytes)` with `.id -> str`, `.add_cipher(descriptor, key: bytes, for_write=True)`; cdef attr `provider: shared_ptr[env_encryption.EncryptionProvider]` (Tasks 2+ use it); module helper `cdef string config_str_to_string(object) except *`.

- [ ] **Step 1: Write the failing tests** — `rocksdb/tests/test_encryption.py`:

```python
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
        # "CTR" without a cipher is incomplete: rocksdb reports NotFound.
        with self.assertRaises(rocksdb.errors.NotFound):
            rocksdb.EncryptionProvider("CTR")

    def test_spec_type_error(self):
        with self.assertRaises(TypeError):
            rocksdb.EncryptionProvider(42)

    def test_add_cipher_passthrough(self):
        # A complete CTR provider rejects extra keys; proves the call reaches
        # rocksdb and the Status maps to our exception hierarchy.
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest rocksdb/tests/test_encryption.py -v`
Expected: every test errors with `AttributeError: module 'rocksdb' has no attribute 'EncryptionProvider'`.

- [ ] **Step 3: Create `rocksdb/env_encryption.pxd`**

```cython
from libcpp cimport bool as cpp_bool
from libcpp.string cimport string

from .std_memory cimport shared_ptr
from .env cimport Env
from .status cimport Status

cdef extern from "rocksdb/convenience.h" namespace "rocksdb":
    cdef cppclass ConfigOptions:
        ConfigOptions() except +
        cpp_bool ignore_unsupported_options

cdef extern from "rocksdb/env_encryption.h" namespace "rocksdb":
    cdef cppclass EncryptionProvider:
        string GetId() except+ nogil
        cpp_bool IsInstanceOf(const string&) except+ nogil
        Status AddCipher(const string&, const char*, size_t, cpp_bool) except+ nogil
        size_t GetPrefixLength() except+ nogil

    cdef Status EncryptionProvider_CreateFromString "rocksdb::EncryptionProvider::CreateFromString"(
        const ConfigOptions&,
        const string&,
        shared_ptr[EncryptionProvider]*) except+ nogil

    cdef Env* NewEncryptedEnv(Env*, const shared_ptr[EncryptionProvider]&) except+ nogil
```

Check `rocksdb/std_memory.pxd` declares `shared_ptr.get()`; if not, extend it there (do not switch to `libcpp.memory` — the repo uses its own).

- [ ] **Step 4: Wire imports in `rocksdb/_rocksdb.pyx`**

After `from . cimport env` (line ~29) add:

```cython
from .env cimport Env as CppEnv
from .env cimport Env_Default
from . cimport env_encryption
```

Next to `import weakref` / `import threading` (line ~64) add:

```cython
import warnings
```

- [ ] **Step 5: Add helper + class** (immediately before `cdef class ColumnFamilyOptions`, line ~783). The error classes used (`InvalidArgument`) are already imported near line 58 — verify, extend the import if not.

```cython
cdef string config_str_to_string(object spec) except *:
    # Config/descriptor strings (EncryptionProvider specs etc.) are text;
    # accept str (utf-8) or bytes.
    if isinstance(spec, str):
        spec = (<str>spec).encode('utf-8')
    if not isinstance(spec, bytes):
        raise TypeError("expected str or bytes, got %s" % type(spec))
    return bytes_to_string(spec)


cdef class EncryptionProvider(object):
    """An encryption provider for encrypted Envs, loaded from librocksdb's
    object registry by its registered name/config string
    (``rocksdb::EncryptionProvider::CreateFromString``).

    Stock RocksDB only registers the test-grade ``CTR`` provider (ROT13
    cipher); production providers (e.g. AES from the encfs/ippcp plugins)
    must be compiled into librocksdb and are addressed by their config
    string, e.g. ``"id=AES;hex_instance_key=...;method=AES256CTR"``.
    """
    cdef shared_ptr[env_encryption.EncryptionProvider] provider

    def __cinit__(self, spec):
        cdef env_encryption.ConfigOptions cfg
        cdef string c_spec = config_str_to_string(spec)
        cdef Status st
        # Strict: an unknown provider name must fail (NotSupported), not
        # silently return OK with a null provider.
        cfg.ignore_unsupported_options = False
        with nogil:
            st = env_encryption.EncryptionProvider_CreateFromString(
                cfg, c_spec, cython.address(self.provider))
        check_status(st)
        if self.provider.get() == NULL:
            # e.g. the empty spec: rocksdb reports OK but creates nothing.
            raise errors.InvalidArgument(
                "encryption provider spec %r did not resolve to a provider"
                % (spec,))
        if self.provider.get().IsInstanceOf(b"CTR"):
            warnings.warn(
                "'%s' resolves to RocksDB's built-in CTR encryption "
                "provider: its only stock cipher is the test-only ROT13 and "
                "its per-file IVs come from a non-cryptographic PRNG. It "
                "does NOT provide real at-rest security; use a production "
                "provider compiled into librocksdb (e.g. an AES plugin) "
                "instead." % (spec,), UserWarning, stacklevel=2)

    @property
    def id(self):
        """The provider's full config id string (``Customizable::GetId``)."""
        cdef string c_id
        c_id = self.provider.get().GetId()
        return c_id[:c_id.size()].decode('utf-8')

    def add_cipher(self, descriptor, key, for_write=True):
        """Hand a raw cipher key to the provider
        (``EncryptionProvider::AddCipher``). Semantics are provider-specific;
        providers that take their key from the config string typically
        reject this with :py:exc:`rocksdb.errors.NotSupported`.
        """
        cdef string c_desc = config_str_to_string(descriptor)
        cdef Status st
        cdef const char* c_key
        cdef size_t c_len
        cdef cpp_bool c_for_write = for_write
        if not isinstance(key, bytes):
            raise TypeError("key must be bytes, got %s" % type(key))
        c_key = PyBytes_AsString(key)
        c_len = PyBytes_Size(key)
        with nogil:
            st = self.provider.get().AddCipher(c_desc, c_key, c_len, c_for_write)
        check_status(st)
```

Adjust to the file's actual error-raising style: if the pyx raises `InvalidArgument(...)` unqualified (check how existing code raises it), match that instead of `errors.InvalidArgument`.

Note `.id` conversion: check the file for an existing std::string→bytes helper (e.g. `string_to_bytes`) and use it if present.

- [ ] **Step 6: Build and run tests**

Run: `python3 setup.py build_ext --inplace && python3 -m pytest rocksdb/tests/test_encryption.py -v`
Expected: all Task-1 tests PASS.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "Expose rocksdb::EncryptionProvider (CreateFromString passthrough)"`

---

### Task 2: `Env` base + `EncryptedEnv`

**Files:**
- Modify: `rocksdb/_rocksdb.pyx` (below `EncryptionProvider`)
- Test: `rocksdb/tests/test_encryption.py` (append)

**Interfaces:**
- Consumes: `EncryptionProvider` (Task 1), `CppEnv`/`Env_Default` cimports, `env_encryption.NewEncryptedEnv`.
- Produces: `rocksdb.Env` (abstract; cdef attr `CppEnv* wrapped_env`), `rocksdb.EncryptedEnv(provider_or_spec)` with readonly `.provider`. Tasks 3–5 consume `Env` (isinstance checks + `wrapped_env`).

- [ ] **Step 1: Failing tests** (append class):

```python
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
```

- [ ] **Step 2: Run** `python3 -m pytest rocksdb/tests/test_encryption.py -k Env -v` — expected: AttributeError (no `rocksdb.Env`).

- [ ] **Step 3: Implement** (right after `EncryptionProvider`):

```cython
cdef class Env(object):
    """Abstract base for RocksDB environments usable as ``Options.env``."""
    cdef CppEnv* wrapped_env

    def __cinit__(self, *args, **kwargs):
        self.wrapped_env = NULL

    def __init__(self, *args, **kwargs):
        raise TypeError(
            "rocksdb.Env is abstract; use rocksdb.EncryptedEnv")


cdef class EncryptedEnv(Env):
    """An Env that transparently encrypts/decrypts every file RocksDB
    writes/reads (SSTs, WAL, MANIFEST, ...), by wrapping the default Env
    with an :py:class:`EncryptionProvider`
    (``rocksdb::NewEncryptedEnv``).

    Accepts an :py:class:`EncryptionProvider` or a provider spec string.
    The env is kept alive automatically by every Options/DB/BackupEngine
    that uses it and is destroyed with its last reference.
    """
    cdef readonly EncryptionProvider provider

    def __init__(self, provider):
        if self.wrapped_env != NULL:
            raise RuntimeError("EncryptedEnv is already initialized")
        if isinstance(provider, (str, bytes)):
            provider = EncryptionProvider(provider)
        elif not isinstance(provider, EncryptionProvider):
            raise TypeError(
                "provider must be a rocksdb.EncryptionProvider or a spec "
                "str/bytes, got %s" % type(provider))
        self.provider = provider
        self.wrapped_env = env_encryption.NewEncryptedEnv(
            Env_Default(), (<EncryptionProvider>provider).provider)

    def __dealloc__(self):
        # NewEncryptedEnv returns a caller-owned Env*. Every DB/BackupEngine
        # using this env holds a reference to this wrapper (see DB.py_env),
        # so when this runs the DB has already been drained and closed;
        # deleting the env here cannot race live RocksDB I/O — including
        # when free-threaded CPython defers this __dealloc__ to another
        # thread.
        if self.wrapped_env != NULL:
            del self.wrapped_env
            self.wrapped_env = NULL
```

- [ ] **Step 4: Build + run** `python3 setup.py build_ext --inplace && python3 -m pytest rocksdb/tests/test_encryption.py -v` — all PASS.
- [ ] **Step 5: Commit** — `git commit -am "Add rocksdb.Env base and EncryptedEnv (NewEncryptedEnv binding)"`

---

### Task 3: `Options.env` property

**Files:**
- Modify: `rocksdb/_rocksdb.pyx` — `cdef class Options` (line ~1219: attrs + property)
- Test: `rocksdb/tests/test_encryption.py` (append)

**Interfaces:**
- Consumes: `Env` (Task 2).
- Produces: `Options.env` property; cdef attr `Options.py_env` (Task 4 pins it from `DB.__cinit__`).

- [ ] **Step 1: Failing tests:**

```python
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
```

- [ ] **Step 2: Run** `-k OptionsEnv` — expected: AttributeError on `opts.env` read (no such property → actually plain `Options` has no `env` attribute, `.env` raises AttributeError).
- [ ] **Step 3: Implement.** Add attr to the `Options` class body (next to `cdef PyCache py_row_cache`):

```cython
    cdef Env py_env
```

Add the property (after `property paranoid_checks`):

```cython
    property env:
        def __get__(self):
            return self.py_env
        def __set__(self, value):
            cdef Env c_env
            if value is None:
                self.opts.env = Env_Default()
                self.py_env = None
                return
            if not isinstance(value, Env):
                raise TypeError(
                    "env must be a rocksdb.Env (e.g. rocksdb.EncryptedEnv) "
                    "or None, got %s" % type(value))
            c_env = <Env>value
            if c_env.wrapped_env == NULL:
                raise errors.InvalidArgument("env is not initialized")
            # Keep the Python reference; rocksdb only copies the raw pointer
            # (at DB open). NOTE: reassigning env on an Options object does
            # not affect a DB that was already opened with it — the DB pins
            # the env it was opened with (DB.py_env).
            self.py_env = c_env
            self.opts.env = c_env.wrapped_env
```

(match the file's actual raising style for `InvalidArgument`, as in Task 1)

- [ ] **Step 4: Build + run** — all PASS.
- [ ] **Step 5: Commit** — `git commit -am "Expose Options.env"`

---

### Task 4: DB/TransactionDB integration + end-to-end encryption tests

**Files:**
- Modify: `rocksdb/_rocksdb.pyx` — `cdef class DB` (line ~1851)
- Test: `rocksdb/tests/test_encryption.py` (append)

**Interfaces:**
- Consumes: `Options.py_env` (Task 3).
- Produces: `DB.py_env` pin (also inherited by `TransactionDB`).

- [ ] **Step 1: Failing tests:**

```python
def make_test_env():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return rocksdb.EncryptedEnv("CTR://test")


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
        opts = rocksdb.Options(create_if_missing=True, **extra)
        if env is not None:
            opts.env = env
        db = cls(os.path.join(self.loc, name), opts)
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
        items = list(db.iteritems())
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
        db.put(b"key", self.MARKER)      # lands in the WAL at least
        db.close()
        self.assertEqual(
            self._files_containing(os.path.join(self.loc, "enc"), self.MARKER),
            [])
        current = os.path.join(self.loc, "enc", "CURRENT")
        with open(current, "rb") as f:
            self.assertFalse(f.read().startswith(b"MANIFEST-"))
        # control: without the env the marker IS on disk
        db2 = self._open(name="plain")
        db2.put(b"key", self.MARKER)
        db2.close()
        self.assertNotEqual(
            self._files_containing(os.path.join(self.loc, "plain"), self.MARKER),
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

    def test_env_reassignment_does_not_free_pinned_env(self):
        env = make_test_env()
        opts = rocksdb.Options(create_if_missing=True, env=env)
        db = rocksdb.DB(os.path.join(self.loc, "pin"), opts)
        self._dbs.append(db)
        db.close()                       # releases the Options claim
        opts.env = make_test_env()       # reassign; old env still pinned by db
        del env
        gc.collect()
        db2 = rocksdb.DB(os.path.join(self.loc, "pin2"), opts)
        self._dbs.append(db2)
        db2.put(b"key", b"value")
        self.assertEqual(db2.get(b"key"), b"value")

    def test_env_shared_by_sequential_dbs(self):
        env = make_test_env()
        for name in ("one", "two"):
            db = self._open(name=name, env=env)
            db.put(b"key", name.encode())
            db.close()
        db = self._open(name="one", env=env)
        self.assertEqual(db.get(b"key"), b"one")

    def test_transaction_db(self):
        env = make_test_env()
        db = self._open(name="txn", env=env, cls=rocksdb.TransactionDB)
        db.put(b"key", b"value")
        self.assertEqual(db.get(b"key"), b"value")
```

Note: `self._open(name="enc2")` inside `assertRaises` appends nothing on failure (DB constructor raises before append) — safe.
If `rocksdb.TransactionDB(path, opts)` requires a `TransactionDBOptions`, pass `tdb_opts=rocksdb.TransactionDBOptions()` — check the constructor signature in `_rocksdb.pyx:2739` and adjust.

- [ ] **Step 2: Run** `-k EncryptedDB` — expected: `test_env_outlives_dropped_references` and friends may PASS already via the DB→opts→py_env chain; `test_env_reassignment_does_not_free_pinned_env` exercises the pin. Run to see the real baseline; any failure modes here are the point of the task.
- [ ] **Step 3: Implement the pin.** In `cdef class DB` body (next to `cdef Options opts`):

```cython
    cdef Env py_env
```

In `DB.__cinit__`, next to `self.opts = None` add `self.py_env = None`; then immediately after the `try_acquire` claim succeeds (first line inside the `try:`), add:

```cython
            # Pin the env attached to these Options for this DB's whole
            # lifetime: rocksdb copies the raw Options.env pointer at Open,
            # so the DB itself must hold the reference — a later
            # `opts.env = ...` reassignment must not free the env this DB
            # still uses. Never cleared in close(): the env has to survive
            # until the phase-2 Close() drain completes; it is released when
            # this DB object is collected, strictly after that.
            self.py_env = opts.py_env
```

- [ ] **Step 4: Build + run the whole file** `python3 setup.py build_ext --inplace && python3 -m pytest rocksdb/tests/test_encryption.py -v` — all PASS. Also run the existing suite in-tree to catch regressions: `python3 -m pytest rocksdb/tests/ -x -q`.
- [ ] **Step 5: Commit** — `git commit -am "Pin Options.env on the DB; end-to-end encrypted-DB tests"`

---

### Task 5: `BackupEngine(env=...)`

**Files:**
- Modify: `rocksdb/_rocksdb.pyx` — `cdef class BackupEngine` (`__cinit__`, line ~3035)
- Test: `rocksdb/tests/test_encryption.py` (append)

**Interfaces:**
- Consumes: `Env` (Task 2).
- Produces: `BackupEngine(backup_dir, env=None)`.

- [ ] **Step 1: Failing test:**

```python
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
```

Check the actual restore method name in `_rocksdb.pyx` (`restore_latest_backup(db_dir, wal_dir)` exists near `restore_backup`) and match it.

- [ ] **Step 2: Run** `-k Backup` — expected: `TypeError: __cinit__() got an unexpected keyword argument 'env'`.
- [ ] **Step 3: Implement.** Replace `BackupEngine.__cinit__` (and add the attr):

```cython
cdef class BackupEngine(object):
    cdef backup.BackupEngine* engine
    # Keeps a passed env alive for the engine's lifetime (the C++ engine
    # holds the raw pointer). Deleted-engine-then-decref order in
    # __dealloc__ guarantees the env outlives the engine.
    cdef Env py_env

    def  __cinit__(self, backup_dir, env=None):
        cdef Status st
        cdef string c_backup_dir
        cdef CppEnv* c_env
        cdef Env env_ob
        self.engine = NULL
        self.py_env = None

        if env is None:
            c_env = Env_Default()
        else:
            if not isinstance(env, Env):
                raise TypeError(
                    "env must be a rocksdb.Env (e.g. rocksdb.EncryptedEnv) "
                    "or None, got %s" % type(env))
            env_ob = <Env>env
            if env_ob.wrapped_env == NULL:
                raise errors.InvalidArgument("env is not initialized")
            c_env = env_ob.wrapped_env
            self.py_env = env_ob

        c_backup_dir = path_to_string(backup_dir)
        st = backup.BackupEngine_Open(
            c_env,
            backup.BackupEngineOptions(c_backup_dir),
            cython.address(self.engine))

        check_status(st)
```

(the parameter `env` shadows the cimported `env` module inside this method — that is why the class uses the direct `CppEnv`/`Env_Default` cimports; keep it that way. Match the file's raising style for `InvalidArgument`.)

- [ ] **Step 4: Build + run** — all encryption tests PASS; run existing backup tests too: `python3 -m pytest rocksdb/tests/ -q -k backup`.
- [ ] **Step 5: Commit** — `git commit -am "BackupEngine: optional env= (backs up/restores encrypted DBs)"`

---

### Task 6: Documentation + changelog

**Files:**
- Create: `docs/api/encryption.rst`
- Modify: `docs/api/index.rst` (toctree), `docs/api/options.rst` (`env` attribute), `docs/api/backup.rst` (`env` kwarg), `docs/changelog.rst` (new "Version 2.4" section), `docs/tutorial/index.rst` (short section)

Content requirements for `docs/api/encryption.rst` (mirror the rst directive style of `docs/api/backup.rst`):
- `EncryptionProvider`, `EncryptedEnv` class/method/attribute docs.
- A worked example (spec string → env → `Options.env` → DB).
- An unmissable warning block: stock RocksDB ships only the test-grade
  CTR/ROT13 provider (the binding emits a `UserWarning` for it); real
  providers must be compiled into librocksdb (encfs/ippcp plugins); the
  scheme is unauthenticated (no integrity); key management is the caller's
  responsibility; filesystem/block-device encryption is the zero-code
  alternative.

- [ ] **Step 1: Write the docs pages** (content per above).
- [ ] **Step 2: Build docs** `sphinx-build -W -b html docs docs/_build/html` — expected: success, zero warnings.
- [ ] **Step 3: Commit** — `git commit -am "Document encryption-at-rest bindings"`

---

### Task 7: Full verification

- [ ] **Step 1:** Clean rebuild + install: `pip install '.[test]'`
- [ ] **Step 2:** Full suite against the installed package: `python3 -m pytest --pyargs rocksdb -q` — expected: everything passes, including all prior suites (lifecycle, concurrency, ...).
- [ ] **Step 3:** Docs: `sphinx-build -W -b html docs docs/_build/html` — passes.
- [ ] **Step 4:** `git status` clean except intended files; review `git diff main` in full.
- [ ] **Step 5:** Final commit if anything outstanding; report status (branch left unmerged for user review).
