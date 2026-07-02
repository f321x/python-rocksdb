# Encryption-at-rest framework bindings — design

**Date:** 2026-07-02
**Status:** approved (stage 1 of the encryption research recommendation)

## Goal

Expose RocksDB's encryption-at-rest *framework* (`rocksdb/env_encryption.h`)
through the binding: load an `EncryptionProvider` by its registered
name/config string, wrap it in an encrypted `Env`, and attach that env to a
DB via `Options.env`. The binding ships **no cryptography of its own**; real
ciphers come from an `EncryptionProvider` compiled into the user's
librocksdb (e.g. the encfs or ippcp plugins). Vendoring an OpenSSL AES-CTR
provider is a possible later stage, out of scope here.

## Background (from the 2026-07-02 research)

- The API is public and stable across RocksDB 8.x–10.x (our supported range)
  and is exported by stock distro `librocksdb.so` — no version guards needed.
- Stock RocksDB ships only the test-grade ROT13 cipher ("should not be used
  in production!!!") and the `CTREncryptionProvider`, whose per-file IVs come
  from a non-cryptographic PRNG. Anything CTR-based must warn.
- `EncryptionProvider::CreateFromString` with default `ConfigOptions`
  silently returns OK + null for unknown provider names; with
  `ignore_unsupported_options = false` it returns NotSupported. The binding
  uses the strict mode **and** raises `InvalidArgument` if the result is
  still null (empty spec).
- Opening an encrypted DB without the env fails with a clean `Corruption`
  (no crash) — verified against librocksdb 9.10.

## Public API

```python
provider = rocksdb.EncryptionProvider("id=AES;hex_instance_key=...;method=AES256CTR")
provider.id                      # -> "AES" (str)
provider.add_cipher(descriptor, key: bytes, for_write=True)  # raw-key passthrough

env = rocksdb.EncryptedEnv(provider)      # or EncryptedEnv("spec string")
env.provider                              # readonly

opts = rocksdb.Options(create_if_missing=True, env=env)
opts.env                                  # property, get/set, None = default env

engine = rocksdb.BackupEngine(backup_dir, env=env)   # new optional kwarg
```

- `rocksdb.Env` is an abstract base (direct instantiation raises
  `TypeError`); `EncryptedEnv` is its only concrete subclass for now.
- Constructing a provider that resolves to the built-in CTR provider
  (`IsInstanceOf("CTR")`) emits a `UserWarning` explaining it is not
  production-grade. It is not refused: RocksDB semantics are preserved and
  our own tests use it.
- Errors surface through the existing `check_status` mapping
  (`NotSupported` for unknown provider names, `NotFound` for `"CTR"` without
  a cipher, etc.).

## Lifetime model (the critical part)

- `EncryptionProvider` holds a `shared_ptr` — trivially safe.
- `EncryptedEnv` **owns** the `Env*` returned by `NewEncryptedEnv` and
  deletes it in `__dealloc__`.
- `DBOptions::env` is a raw non-owned pointer that RocksDB copies at
  `Open()`; the env must outlive the DB. Therefore:
  - `Options` keeps a `py_env` reference (existing `py_comparator` pattern),
  - **`DB` pins `opts.py_env` into its own `py_env` at construction**, so a
    later `opts.env = ...` reassignment cannot free an env the open DB still
    uses,
  - `DB.close()` does *not* drop `py_env` (the env must survive until the
    phase-2 `Close()` drain completes); it is released when the DB object is
    collected, strictly after `Close()`. On free-threaded builds a deferred
    cross-thread `__dealloc__` of the env is then harmless — nothing touches
    the pointer post-Close.
  - `BackupEngine` likewise keeps `py_env` and deletes the engine before the
    reference is dropped.

## Testing (CI)

Use RocksDB's **built-in CTR/ROT13 test provider** (`"CTR://test"`,
`"id=CTR;cipher=ROT13"`) — no Python-implemented cipher. Rationale: a Python
`BlockCipher` trampoline would be new framework surface (GIL from RocksDB
background threads, per-16-byte-block callbacks) that production users would
never run; the string-loaded provider exercises the *identical* code path a
real plugin provider takes, on stock librocksdb, in both existing CI
workflows (`build.yml`, `debian.yml`) via `pytest --pyargs rocksdb` with no
workflow changes.

Key test assertions (`rocksdb/tests/test_encryption.py`):
- provider creation / `id` / CTR `UserWarning` / unknown-name `NotSupported`
  / empty-spec `InvalidArgument` / bare-`"CTR"` `NotFound` /
  `add_cipher` `NotSupported` passthrough on a complete CTR provider
- encrypted DB put/get/flush/reopen round-trip; TransactionDB round-trip
- plaintext marker absent from every file on disk (control: present in a
  plain DB's WAL); `CURRENT` not starting with `MANIFEST-`
- opening the encrypted DB without the env raises (`Corruption`)
- env survives `del env; del opts; gc.collect()` while DB is open; env
  reusable across sequential DBs
- backup + restore of an encrypted DB through `BackupEngine(..., env=env)`

## Docs

New `docs/api/encryption.rst` (classes + a worked example + explicit
warnings: CTR/ROT13 is test-only, no integrity/AEAD, key management is the
caller's problem, plugin providers must be compiled into librocksdb, OS-level
encryption is the zero-code alternative); `Options.env` in
`docs/api/options.rst`; `env=` in `docs/api/backup.rst`; changelog entry
under a new "Version 2.4" heading.

## Out of scope (later stages)

- Vendored OpenSSL AES-CTR provider (stage 2 of the recommendation).
- Python-implemented `BlockCipher`/`EncryptionProvider` trampolines.
- `NewEncryptedFS` / `FileSystem`-level wrapping, `MemEnv`, per-column-family
  envs.
