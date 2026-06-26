Thread safety and free-threading
================================

``rocksdb-ng`` supports the free-threaded ("nogil") builds of CPython. The
extension declares itself free-threading compatible, so importing it on a
free-threaded interpreter does **not** re-enable the GIL.

This page describes what may and may not be shared between threads.

What is safe to share
---------------------

A single :class:`~rocksdb.DB` (or :class:`~rocksdb.TransactionDB`) object may be
used concurrently from many threads for:

* the data path — ``get``, ``put``, ``delete``, ``write``, ``multi_get``,
  ``key_may_exist`` — which runs lock-free and relies on RocksDB's own internal
  thread-safety;
* creating iterators and snapshots (``iterkeys``/``itervalues``/``iteritems``,
  ``snapshot``);
* column-family management (``create_column_family``, ``drop_column_family``,
  ``get_column_family``, ``column_families``), which is serialized per DB by an
  internal lock;
* ``close`` — concurrent and repeated ``close()`` calls are safe and idempotent,
  as is a garbage-collected ``__dealloc__`` running on another thread.

The closing contract
--------------------

The data path is intentionally lock-free for parallelism. The contract is:

    **Do not close a DB while other threads are still operating on it.**

``close()`` invalidates the underlying RocksDB handle; a ``get``/``put`` that is
in flight on another thread at that moment is a use-after-free, exactly as it
would be a logic error under the GIL. Make sure all worker threads have stopped
using a DB before you close it. (Closing concurrently with *other* ``close()``
calls, or with a GC-driven finalization, is safe — that is handled internally.)

Objects confined to one thread
------------------------------

The following are **not** safe to share across threads. Confine each to the
thread that created it:

* **Iterators** (the objects returned by ``iterkeys`` / ``itervalues`` /
  ``iteritems`` and their ``reversed`` views). A ``rocksdb::Iterator`` is a
  single cursor with mutable position; sharing one races the cursor, just as
  Python's own iterators are not thread-safe.
* **WriteBatch**. A batch is a single-writer builder; build it on one thread,
  then hand the finished batch to ``DB.write``.

Options objects
---------------

An :class:`~rocksdb.Options`, :class:`~rocksdb.ColumnFamilyOptions`, or
:class:`~rocksdb.TransactionDBOptions` object is claimed when it is attached to a
DB or column family and cannot be reused for another until the owner is closed.
Attempting to use one mutable options object for two DBs raises
``InvalidArgument`` (or a ``ValueError`` for column families) — and this check is
atomic, so two threads racing to open DBs with the same options object will see
exactly one winner.

Custom comparators and merge operators
--------------------------------------

User-supplied :class:`~rocksdb.interfaces.Comparator`,
:class:`~rocksdb.interfaces.MergeOperator`,
:class:`~rocksdb.interfaces.AssociativeMergeOperator`, and
:class:`~rocksdb.interfaces.SliceTransform` implementations **must be internally
thread-safe**. RocksDB invokes these callbacks concurrently from multiple
background threads (for example, parallel subcompactions), and the binding does
not serialize them — doing so would throw away the parallelism free threading
exists to provide. Keep them stateless, or guard any shared state yourself.

Using a free-threaded build
---------------------------

Install a free-threaded interpreter (e.g. ``python3.14t``) and run with the GIL
disabled::

    PYTHON_GIL=0 python3.14t -c "import rocksdb, sys; print(sys._is_gil_enabled())"

That prints ``False``: importing ``rocksdb`` leaves the GIL disabled. The
project's CI exercises this on the ``3.14t`` build and additionally runs the
concurrency suite under ThreadSanitizer (a ThreadSanitizer-instrumented
interpreter and RocksDB) to catch data races.
