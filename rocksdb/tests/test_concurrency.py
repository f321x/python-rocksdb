"""Concurrency / free-threading stress tests.

These exercise the data races that the free-threading work addresses (see
``docs/superpowers/specs/2026-06-26-free-threading-compatibility-design.md``):

  * B1 — concurrent ``create_column_family`` / ``drop_column_family``
  * B2 — concurrent ``close()`` (and GC-driven ``__dealloc__``)
  * B3 — one ``Options`` handed to several ``DB()`` constructors at once
  * B6 — snapshot teardown racing ``close()``
  * B7 — concurrent ``_ColumnFamilyHandle.weakref`` lazy-cache access

They are written to stay inside the documented thread-safety contract (a DB is
not closed while other threads still operate on it), so they are deterministic
on *every* build:

  * On a GIL build they still run with real threads and a real ``pymutex``, so
    they catch lock-ordering deadlocks, list desync, and logic errors.
  * On a free-threaded build (``PYTHON_GIL=0``) they actually run in parallel,
    and under ThreadSanitizer the unsynchronized accesses would be reported.

Run a single one with::

    pytest --pyargs rocksdb.tests.test_concurrency -k weakref
"""

import gc
import sys
import sysconfig
import threading

import pytest

import rocksdb
from rocksdb.errors import InvalidArgument

NUM_THREADS = 8


def _is_freethreaded_build():
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def _run_threaded(target, n=NUM_THREADS):
    """Run ``target(index, barrier)`` on ``n`` threads; re-raise the first error.

    The barrier makes all threads start the contended work at the same instant,
    which maximises the chance of hitting an interleaving bug.
    """
    barrier = threading.Barrier(n)
    errors = []

    def wrapper(i):
        try:
            target(i, barrier)
        except Exception as exc:  # noqa: BLE001 - report it from the main thread
            errors.append(exc)

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def _open(tmp_path, name="db"):
    return rocksdb.DB(str(tmp_path / name), rocksdb.Options(create_if_missing=True))


# --------------------------------------------------------------------------- #
# B3: one mutable Options must not back two DBs                                #
# --------------------------------------------------------------------------- #
def test_shared_options_claimed_by_exactly_one_db(tmp_path):
    opts = rocksdb.Options(create_if_missing=True)
    winners = []
    rejected = []

    def attempt(i, barrier):
        barrier.wait()
        try:
            db = rocksdb.DB(str(tmp_path / f"db_{i}"), opts)
            winners.append(db)
        except InvalidArgument:
            rejected.append(i)

    errors = _run_threaded(attempt)
    assert not errors, errors
    # Exactly one DB may claim the shared Options; everyone else is rejected.
    assert len(winners) == 1, f"{len(winners)} DBs claimed one Options"
    assert len(rejected) == NUM_THREADS - 1
    for db in winners:
        db.close()


# --------------------------------------------------------------------------- #
# B1: concurrent column-family create/drop keeps the parallel lists in sync   #
# --------------------------------------------------------------------------- #
def test_concurrent_create_drop_column_family(tmp_path):
    db = _open(tmp_path)
    try:
        def churn(i, barrier):
            barrier.wait()
            for j in range(40):
                name = f"cf_{i}_{j}".encode()
                handle = db.create_column_family(name, rocksdb.ColumnFamilyOptions())
                db.drop_column_family(handle)

        errors = _run_threaded(churn)
        assert not errors, errors
        # All created CFs were dropped; only the default remains, and the two
        # parallel lists (cf_handles / cf_options) are still consistent — if they
        # had desynced, building this list would raise or be wrong.
        assert len(db.column_families) == 1
    finally:
        db.close()


def test_concurrent_create_same_name_single_winner(tmp_path):
    db = _open(tmp_path)
    try:
        created = []

        def attempt(i, barrier):
            barrier.wait()
            try:
                db.create_column_family(b"shared", rocksdb.ColumnFamilyOptions())
                created.append(i)
            except ValueError:
                pass  # "already an existing column family"

        errors = _run_threaded(attempt)
        assert not errors, errors
        assert len(created) == 1, f"{len(created)} threads created the same CF"
        names = {h.name for h in db.column_families}
        assert b"shared" in names
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# B2: concurrent close() is idempotent (no double Close / double-free)         #
# --------------------------------------------------------------------------- #
def test_concurrent_close_is_idempotent(tmp_path):
    db = _open(tmp_path)
    db.put(b"k", b"v")

    def closer(i, barrier):
        barrier.wait()
        for _ in range(10):
            db.close()

    errors = _run_threaded(closer)
    assert not errors, errors


def test_close_after_gc_dealloc(tmp_path):
    # A DB dropped on a worker thread is finalized (close via __dealloc__) there,
    # concurrently with unrelated DBs being closed on other threads. Must not
    # crash or deadlock.
    def make_and_drop(i, barrier):
        barrier.wait()
        for _ in range(20):
            db = _open(tmp_path, f"db_{i}")
            db.put(b"k", b"v")
            del db
            gc.collect()

    errors = _run_threaded(make_and_drop, n=4)
    assert not errors, errors


# --------------------------------------------------------------------------- #
# Lock-free data path stays correct under concurrent use                       #
# --------------------------------------------------------------------------- #
def test_concurrent_reads_and_writes(tmp_path):
    db = _open(tmp_path)
    try:
        def work(i, barrier):
            barrier.wait()
            for j in range(200):
                key = f"k_{i}_{j}".encode()
                db.put(key, b"v")
                assert db.get(key) == b"v"

        errors = _run_threaded(work)
        assert not errors, errors
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# B6: snapshot/iterator churn racing each other (within the close contract)    #
# --------------------------------------------------------------------------- #
def test_snapshot_and_iterator_churn(tmp_path):
    db = _open(tmp_path)
    for k in range(200):
        db.put(f"k{k}".encode(), b"v")
    try:
        def churn(i, barrier):
            barrier.wait()
            for _ in range(50):
                snap = db.snapshot()
                it = db.iterkeys()
                it.seek_to_first()
                for _, _key in zip(range(5), it):
                    pass
                del snap, it
            gc.collect()

        errors = _run_threaded(churn)
        assert not errors, errors
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# B7: the CF-handle weakref lazy cache must not diverge across threads         #
# --------------------------------------------------------------------------- #
def test_weakref_cache_hash_eq_consistency(tmp_path):
    db = _open(tmp_path)
    try:
        db.create_column_family(b"cf", rocksdb.ColumnFamilyOptions())
        # get_column_family() returns the public weakref wrapper minted by the
        # _ColumnFamilyHandle.weakref lazy cache. Hammer it from many threads and
        # assert every wrapper for the one CF is interchangeable.
        results = []
        lock = threading.Lock()

        def grab(i, barrier):
            barrier.wait()
            local = [db.get_column_family(b"cf") for _ in range(50)]
            with lock:
                results.extend(local)

        errors = _run_threaded(grab)
        assert not errors, errors
        # A divergent lazy cache would yield wrappers that compare equal but hash
        # differently, breaking the set/dict-key invariant checked here.
        first = results[0]
        assert all(w == first for w in results)
        assert len({hash(w) for w in results}) == 1
        assert len(set(results)) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Trampolines: a custom comparator + merge operator under concurrent writes    #
# --------------------------------------------------------------------------- #
class _ReverseComparator(rocksdb.interfaces.Comparator):
    def compare(self, a, b):
        return -1 if a < b else (1 if a > b else 0)

    def name(self):
        return b"test.reverse"


def test_concurrent_custom_comparator_and_merge(tmp_path):
    from rocksdb.merge_operators import StringAppendOperator

    opts = rocksdb.Options(create_if_missing=True)
    opts.comparator = _ReverseComparator()
    opts.merge_operator = StringAppendOperator()
    db = rocksdb.DB(str(tmp_path / "cmp"), opts)
    try:
        def work(i, barrier):
            barrier.wait()
            for j in range(100):
                db.merge(f"k{i}".encode(), str(j).encode())

        errors = _run_threaded(work)
        assert not errors, errors
        # Every per-thread key accumulated its 100 merged operands.
        for i in range(NUM_THREADS):
            value = db.get(f"k{i}".encode())
            assert value is not None
            assert len(value.split(b",")) == 100
    finally:
        db.close()


def test_close_drains_background_callbacks_without_deadlock(tmp_path):
    # Regression: close() must run the blocking RocksDB drain OUTSIDE the
    # lifecycle lock. A user comparator/merge runs on background compaction
    # threads; if a snapshot of this DB is GC-finalized on such a thread while
    # close() drains them, the finalizer takes the lifecycle lock — which close()
    # must not be holding, or the two deadlock. We churn snapshots/iterators
    # (creating GC pressure) against a custom-comparator DB, then close.
    from rocksdb.merge_operators import StringAppendOperator

    for _ in range(5):
        opts = rocksdb.Options(create_if_missing=True)
        opts.comparator = _ReverseComparator()
        opts.merge_operator = StringAppendOperator()
        opts.write_buffer_size = 4096  # force flushes/compactions
        db = rocksdb.DB(str(tmp_path / f"db_{_}"), opts)
        stop = threading.Event()

        def churn():
            while not stop.is_set():
                try:
                    snap = db.snapshot()
                    it = db.iterkeys()
                    it.seek_to_first()
                    for _i, _k in zip(range(3), it):
                        pass
                    del snap, it
                    gc.collect()
                except Exception:
                    return

        def write():
            i = 0
            while not stop.is_set():
                db.merge(f"k{i % 50}".encode(), b"v" * 64)
                i += 1

        threads = [threading.Thread(target=churn) for _ in range(3)]
        threads += [threading.Thread(target=write) for _ in range(2)]
        for t in threads:
            t.start()
        for _ in range(200):
            db.put(b"p", b"q")
        stop.set()
        for t in threads:
            t.join()
        db.close()  # drains background compaction threads; must not deadlock
        del db, opts
        gc.collect()


# --------------------------------------------------------------------------- #
# The FT leg must actually run without the GIL                                 #
# --------------------------------------------------------------------------- #
def test_freethreading_does_not_reenable_gil():
    if not _is_freethreaded_build():
        pytest.skip("not a free-threaded interpreter")
    if not hasattr(sys, "_is_gil_enabled"):
        pytest.skip("sys._is_gil_enabled() unavailable")
    # If the extension failed to declare freethreading_compatible, importing it
    # (already done at module load) would have re-enabled the GIL.
    assert not sys._is_gil_enabled(), (
        "importing rocksdb re-enabled the GIL — is freethreading_compatible set?"
    )
