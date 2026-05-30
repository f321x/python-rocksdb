import os
import sys
import shutil
import gc
import unittest
import rocksdb
from itertools import takewhile
import struct
import tempfile
from rocksdb.merge_operators import UintAddOperator, StringAppendOperator

from .test_db import TestHelper, TestDB

class TestTransactionDB(TestDB):
    """Re-run TestDB with the TransactionDB object.

    The binding exposes TransactionDB only as a DB subclass with extra options;
    it does *not* wrap rocksdb::Transaction, so there is no begin/commit/rollback
    or get_for_update surface. Real transaction-semantics tests (isolation,
    conflict detection, rollback, savepoints) are therefore intentionally absent
    -- they would require new binding code, not just new tests. Exposing
    interactive transactions is tracked as a separate future enhancement.

    Re-running TestDB here is not just redundant coverage: the inherited
    close()/double-close/use-after-close tests exercise the TransactionDB.close()
    override, which must reset the db pointer exactly like DB.close() so the
    closed-handle guard works for both.
    """

    def setUp(self):
        TestHelper.setUp(self)
        opts = rocksdb.Options(create_if_missing=True)
        tdb_opts = rocksdb.TransactionDBOptions()
        self.db = rocksdb.TransactionDB(
            os.path.join(self.db_loc, "test"),
            opts,
            tdb_opts=tdb_opts)

    def test_options_used_twice(self):
        expected = "Options object is already used by another DB"
        tdb_opts = rocksdb.TransactionDBOptions()
        with self.assertRaisesRegex(rocksdb.InvalidArgument, expected):
            rocksdb.TransactionDB(os.path.join(self.db_loc, "test2"),
                                  self.db.options,
                                  tdb_opts=tdb_opts)

    def test_transaction_options_used_twice(self):
        expected = "Transaction Options object is already used by another DB"
        opts = rocksdb.Options(create_if_missing=True)
        with self.assertRaisesRegex(rocksdb.InvalidArgument, expected):
            rocksdb.TransactionDB(os.path.join(self.db_loc, "test2"),
                                  opts,
                                  tdb_opts=self.db.transaction_options)
