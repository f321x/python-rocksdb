Encryption At Rest
******************

RocksDB can transparently encrypt every file it writes (SSTs, WAL, MANIFEST,
CURRENT, ...) when the database is opened with an *encrypted Env*: a wrapper
around the default environment that pipes all file I/O through an
:py:class:`rocksdb.EncryptionProvider`. This binding exposes that framework —
it ships **no cryptography of its own**.

.. warning::

    Stock RocksDB contains **no production-grade cipher**. Its only built-in
    provider is the ``CTR`` provider, whose sole bundled cipher is ROT13 —
    upstream marks it *"only suitable for test purposes and should not be
    used in production!!!"* — and whose per-file IVs come from a
    non-cryptographic PRNG. Constructing it therefore emits a
    :py:exc:`UserWarning`.

    Real at-rest security requires an encryption provider compiled into your
    ``librocksdb`` — for example the OpenSSL-based AES-CTR provider from the
    `encfs plugin <https://github.com/pegasus-kv/encfs>`_ or Intel's
    `ippcp plugin <https://github.com/intel/ippcp-plugin-rocksdb>`_ (see
    RocksDB's ``PLUGINS.md``). Such providers are addressed by their config
    string, e.g. ``"id=AES;hex_instance_key=...;method=AES256CTR"``.

    Also note the scheme's inherent limits: CTR-mode encryption provides
    confidentiality only (**no integrity/authentication** — an attacker with
    disk access can flip bits undetected), and **key management — storage,
    rotation, wrapping — is entirely your responsibility**. Filesystem or
    block-device encryption (LUKS/dm-crypt, fscrypt, encrypted EBS) remains
    the zero-code alternative when whole-host encryption is acceptable.

Example (test provider — see the warning above)::

    import rocksdb

    env = rocksdb.EncryptedEnv("CTR://test")   # emits UserWarning: test-grade
    opts = rocksdb.Options(create_if_missing=True, env=env)
    db = rocksdb.DB("test.db", opts)
    db.put(b"key", b"value")

    # Every file in test.db/ is now encrypted; reopening the database
    # requires an env with the same provider config. Opening it without
    # one fails with rocksdb.errors.Corruption.

Backups of an encrypted database work by handing the same env to the backup
engine (the backup files are then encrypted with the same provider)::

    engine = rocksdb.BackupEngine("test.db/backups", env=env)
    engine.create_backup(db, flush_before_backup=True)
    engine.restore_latest_backup("restored.db", "restored.db")


EncryptionProvider
==================

.. py:class:: rocksdb.EncryptionProvider

    .. py:method:: __init__(spec)

        Loads an encryption provider from librocksdb's object registry via
        ``rocksdb::EncryptionProvider::CreateFromString``.

        Loading is strict: a name that is not registered in your
        ``librocksdb`` raises :py:exc:`rocksdb.errors.NotSupported`; a spec
        that resolves to nothing (e.g. the empty string) raises
        :py:exc:`rocksdb.errors.InvalidArgument`; an incomplete provider
        (e.g. plain ``"CTR"``, which lacks a cipher) surfaces RocksDB's own
        error, e.g. :py:exc:`rocksdb.errors.NotFound`.

        :param spec: The provider's registered name or config string, e.g.
                     ``"CTR://test"`` (built-in, **test only**) or
                     ``"id=AES;hex_instance_key=...;method=AES256CTR"``
                     (encfs plugin).
        :type spec: unicode, bytes

    .. py:attribute:: id

        The provider's full config id string
        (``rocksdb::Customizable::GetId``), e.g. ``"CTR"``. Read-only.

    .. py:method:: add_cipher(descriptor, key, for_write=True)

        Hands a raw cipher key to the provider
        (``rocksdb::EncryptionProvider::AddCipher``). Semantics are
        provider-specific; providers that take their key material from the
        config string (like the complete built-in CTR provider) reject this
        with :py:exc:`rocksdb.errors.NotSupported`.

        :param descriptor: Provider-specific key descriptor.
        :type descriptor: unicode, bytes
        :param bytes key: The raw key material.
        :param bool for_write: If ``True`` the key is used for writing new
                               files, otherwise only for reading existing
                               ones.


EncryptedEnv
============

.. py:class:: rocksdb.Env

    Abstract base class for environments assignable to
    :py:attr:`rocksdb.Options.env`. Cannot be instantiated directly.

.. py:class:: rocksdb.EncryptedEnv

    .. py:method:: __init__(provider)

        Creates an Env that encrypts/decrypts all file I/O through the given
        provider, wrapping the default Env
        (``rocksdb::NewEncryptedEnv``).

        The C++ env is owned by this object and freed with its last
        reference. Every :py:class:`rocksdb.DB`,
        :py:class:`rocksdb.TransactionDB` and
        :py:class:`rocksdb.BackupEngine` opened with it keeps it alive
        automatically, so the env can never be destroyed while a database
        still uses it — even if you drop your own references or reassign
        :py:attr:`rocksdb.Options.env`.

        :param provider: The encryption provider, or a spec string which is
                         shorthand for
                         ``EncryptedEnv(rocksdb.EncryptionProvider(spec))``.
        :type provider: :py:class:`rocksdb.EncryptionProvider`, unicode,
                        bytes

    .. py:attribute:: provider

        The :py:class:`rocksdb.EncryptionProvider` this env encrypts with.
        Read-only.
