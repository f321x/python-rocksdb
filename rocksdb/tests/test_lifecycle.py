"""Regression tests for DB teardown ordering.

Closing a DB while an iterator or snapshot is still alive used to crash the
process: an open ``rocksdb::Iterator`` pins a column-family SuperVersion, so
``DB::Close()`` hit ``ColumnFamilySet::~ColumnFamilySet(): Assertion 'last_ref'
failed`` (db/column_family.cc), and a live snapshot dereferenced an
already-NULLed ``wrapped_db`` in ``Snapshot.__dealloc__`` and segfaulted.

These run in a subprocess because the failure modes are an abort/segfault that
cannot be caught in-process.
"""

import os
import subprocess
import sys

import rocksdb

# Make the subprocess import the *same* rocksdb the test imported, whether that
# is the in-tree build or an installed package.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(rocksdb.__file__)))

_OPEN_DB = (
    "import rocksdb\n"
    "opts = rocksdb.Options(create_if_missing=True)\n"
    "db = rocksdb.DB(DB_PATH, opts)\n"
)

_OPEN_TDB = (
    "import rocksdb\n"
    "opts = rocksdb.Options(create_if_missing=True)\n"
    "tdb_opts = rocksdb.TransactionDBOptions()\n"
    "db = rocksdb.TransactionDB(DB_PATH, opts, tdb_opts=tdb_opts)\n"
)


def _run(open_stmt, db_path, body):
    script = open_stmt.replace("DB_PATH", repr(db_path)) + body + "\nprint('CLEAN_EXIT')\n"
    env = dict(os.environ)
    env["PYTHONPATH"] = _PKG_PARENT + os.pathsep + env.get("PYTHONPATH", "")
    # `-P` keeps the CWD off sys.path[0]. Without it, when the tests run from a
    # source checkout (CWD = repo root), `python -c` prepends '' ahead of the
    # PYTHONPATH entry above, so the subprocess imports the un-built in-tree
    # `rocksdb/` (no compiled `_rocksdb`) and dies with ModuleNotFoundError
    # instead of the installed package the parent test actually imported.
    return subprocess.run(
        [sys.executable, "-P", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _assert_clean(res):
    assert res.returncode == 0, (
        f"process exited with {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "CLEAN_EXIT" in res.stdout, f"missing CLEAN_EXIT\nSTDOUT:\n{res.stdout}"


def test_close_with_live_iterator_does_not_abort(tmp_path):
    res = _run(_OPEN_DB, str(tmp_path / "db"),
               "db.put(b'a', b'1'); db.put(b'b', b'2')\n"
               "it = db.iterkeys(); it.seek_to_first()\n"
               "db.close()\n")
    _assert_clean(res)


def test_close_with_live_snapshot_does_not_crash(tmp_path):
    # snapshot is kept alive into interpreter shutdown
    res = _run(_OPEN_DB, str(tmp_path / "db"),
               "db.put(b'a', b'1')\n"
               "snap = db.snapshot()\n"
               "db.close()\n")
    _assert_clean(res)


def test_close_with_multiple_live_iterators_does_not_abort(tmp_path):
    res = _run(_OPEN_DB, str(tmp_path / "db"),
               "db.put(b'a', b'1'); db.put(b'b', b'2')\n"
               "its = [db.iterkeys(), db.itervalues(), db.iteritems()]\n"
               "for it in its:\n"
               "    it.seek_to_first()\n"
               "db.close()\n")
    _assert_clean(res)


def test_using_iterator_after_close_raises_cleanly(tmp_path):
    res = _run(_OPEN_DB, str(tmp_path / "db"),
               "db.put(b'a', b'1')\n"
               "it = db.iterkeys(); it.seek_to_first()\n"
               "db.close()\n"
               "try:\n"
               "    it.seek_to_first()\n"
               "    print('NO_EXCEPTION')\n"
               "except Exception as e:\n"
               "    print('RAISED', type(e).__name__)\n")
    _assert_clean(res)
    assert "RAISED" in res.stdout, f"expected a clean exception\nSTDOUT:\n{res.stdout}"


def test_transactiondb_close_with_live_iterator_does_not_abort(tmp_path):
    res = _run(_OPEN_TDB, str(tmp_path / "tdb"),
               "db.put(b'a', b'1'); db.put(b'b', b'2')\n"
               "it = db.iterkeys(); it.seek_to_first()\n"
               "db.close()\n")
    _assert_clean(res)
