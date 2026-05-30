Changelog
*********

Version 2.1
-----------

Bug-fix release that also lowers the minimum supported RocksDB to 8.x and makes
the build fail fast on an unsupported RocksDB.

Bug fixes
~~~~~~~~~

* **Fixed** ``rocksdb.DB.iterskeys`` **on column families.** A duplicate method
  definition shadowed the real one, so calling ``iterskeys`` with a list of
  column-family handles returned *items* iterators (yielding ``(key, value)``
  pairs) instead of keys iterators. It now correctly returns keys iterators.
* **Added** ``rocksdb.DB.itersitems``, the multi-column-family items iterator
  that the shadowed definition above was meant to provide; it was previously
  unreachable (raised ``AttributeError``).
* **Operating on a closed database now raises** ``RuntimeError`` **instead of
  crashing.** Calling ``put`` / ``get`` / ``delete`` / iterator / etc. after
  ``rocksdb.DB.close()`` previously dereferenced a NULL pointer and segfaulted;
  it now raises a clean exception, and ``close()`` is safe to call more than once.
* **Fixed a double-close in** ``rocksdb.TransactionDB``. ``close()`` did not reset
  its internal handle, so the underlying ``TransactionDB::Close()`` ran twice
  (the explicit ``close()`` plus the one in ``__dealloc__``), risking a crash.

RocksDB version support
~~~~~~~~~~~~~~~~~~~~~~~~~

* **Lowered the minimum supported RocksDB to 8.x** (from 9). The binding builds
  and passes the test suite against RocksDB 8.9, so the supported/tested range is
  now 8.x–10.x.
* **The build now fails fast on an unsupported RocksDB.** ``setup.py`` detects the
  RocksDB version (via ``pkg-config`` / ``<rocksdb/version.h>``) and aborts with a
  clear message when it is older than the supported floor, instead of breaking
  later with an opaque compiler error. A version newer than the tested range only
  warns. The force-included ``rocksdb/cpp/version_check.hpp`` is a compile-time
  backstop for when the version cannot be detected before compiling.

Version 2.0
-----------

Modernization release: builds and passes the test suite against current
RocksDB (tested on 9.10 and 10.10), CPython 3.11–3.14, and Cython 3.

Requirements
~~~~~~~~~~~~

* Requires **RocksDB >= 9**. The extension is compiled with ``-std=c++20``
  because the RocksDB 10.x headers use C++20 features, so a C++20-capable
  compiler is required.
* Requires **CPython 3.11–3.14**; Python 3.10 and earlier are no longer
  supported.
* Building from source requires **Cython >= 3.2.5** and **setuptools >= 80**
  (declared in ``pyproject.toml``).

Backwards-incompatible changes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These follow the removal of the corresponding APIs from upstream RocksDB:

* **Custom Python filter policies are no longer supported.** RocksDB removed the
  legacy ``FilterPolicy::CreateFilter``/``KeyMayMatch`` ("v1") interface, so
  ``rocksdb.interfaces.FilterPolicy`` has been removed and passing a custom
  filter object to :py:class:`rocksdb.BlockBasedTableFactory` now raises
  ``TypeError``. The built-in :py:class:`rocksdb.BloomFilterPolicy` is still
  supported; its ``bits_per_key`` argument is now a float.
* :py:class:`rocksdb.BlockBasedTableFactory` no longer accepts the
  ``hash_index_allow_collision`` or ``block_cache_compressed`` keyword
  arguments (both removed from RocksDB).
* The following :py:class:`rocksdb.Options` attributes were removed because the
  underlying RocksDB fields were deleted: ``base_background_compactions``,
  ``max_background_compactions``, ``new_table_reader_for_compaction_inputs``,
  ``preserve_deletes``, ``access_hint_on_compaction_start`` (and the
  ``AccessHint`` enum), ``max_mem_compaction_level``,
  ``random_access_max_buffer_size``, and ``fail_if_options_file_error`` (the
  last two were removed in RocksDB 10.x).
* The ``zstdnotfinal_compression`` :py:class:`rocksdb.CompressionType` was
  removed (RocksDB 10.x deleted the ``kZSTDNotFinalCompression`` enumerator);
  use ``zstd_compression`` instead.

Internal
~~~~~~~~

* Migrated the Cython sources to Cython 3: callbacks invoked from C++
  (comparator, merge operator, slice transform) are now declared ``noexcept``
  so that exceptions raised in Python are reported as Python exceptions instead
  of unwinding into C++.
* Fixed error-message propagation from callbacks under Python 3 (the previous
  ``<bytes>str(...)`` cast produced garbage on Python 3).
* Updated the BackupEngine bindings to the modern header
  (``rocksdb/utilities/backup_engine.h``) and ``BackupEngineOptions`` type.
* Reworked the CI matrix and wheel build for Python 3.11–3.14 and RocksDB 9/10.
* The documentation is now built and published by a GitHub Actions workflow to
  GitHub Pages (https://f321x.github.io/python-rocksdb/), replacing the old
  readthedocs.org site. It renders with Sphinx's bundled default theme.

Version 0.8
-----------

Yet Another Fork, started by `Martina Ferrari`_, collecting loose commits from the
many forks of the original project. Summary of commits:

* Alexander Böhn
  
  * Allow :py:class:`rocksdb.DB` instances to be manually closed.

* `@iFA88`_
 
  * Many tidying changes.
  * Added support for many parameters in different interfaces.
  * Create statistics.pxd
  * Fixing closing

* `Andrey Martyanov`_

  * Build wheel packages
  * Update README with simplified installation procedure

* `Martina Ferrari`_

  * Fix a few typos.
  * Add as_dict option to multi_get.
  * Update README, set myself as current author/maintainer, and move most
    of setup.py to the configuration file.

Version 0.7
-----------

Version released by `Ming-Hsuan-Tu`_; summary of commits:

* `Ming-Hsuan-Tu`_

  * remove full_scan_mode
  * change default compaction_pri

* meridianz

  * Docs: fix typo in installation command line

* Roman Zeyde

  * Remove `fetch=False` unsupported keyword from
    db.iter{items,keys,values} documentation

* Abhiram R

  * Modified docs to export CPLUS_INCLUDE_PATH, LD_LIBRARY_PATH and
    LIBRARY_PATH correctly even if they weren't originally assigned
  * Added liblz4-dev as a package to be installed

* Jason Fried

  * Column Family Support. Add support for Column Families in a runtime
    safe way. Add unittests to test functionality Insure all unittests are
    passing. Cleaned up unittests to not use a fixed directory in tmp, but
    use tempfile

Version 0.6
-----------

Version released by `Ming-Hsuan-Tu`_; summary of commits:

* `Ming-Hsuan-Tu`_

  * now support rocksdb 5.3.0
  * Merge options source_compaction_factor, max_grandparent_overlap_bytes
    and expanded_compaction_factor into max_compaction_bytes
  * add default merge operator
  * add compaction_pri
  * add seekForPrev
  * update the usage of default operators
  * fix memtable_factory crash
  * add testcase for memtable

* George Mossessian

  * allow snappy_compression as a default option in
    test_options.py::TestOptions::test_simple

* RIMPY BHAROT

  * Update installation.rst. Missing steps need to be added for clean
    installation.

* Chris Hager

  * OSX support for 'pip install'

* Mehdi Abaakouk

  * Allow to compile the extension everywhere.


Version 0.5
-----------

Last version released by `Stephan Hofmockel`_; summary of commits:

* Remove prints from the tests.
* Use another compiler flag wich works for clang and gcc.
* Wrap the `RepairDB` function.
* Get rid of this `extension_defaults` variable.
* Only `cythonize` if Cython is installed.
* Add the `.hpp` `.pxd` `.pyx` files for the sdist.
* Rename `README.md` to `README.rst` so `setup.py` can pick it up.
* Update the installation page by mentioning a "system wide" rocksdb
  installation.
* Improve the `README.rst` by adding a quick install/using guide.
* Don't set a theme explicitly. Let `readthedocs` decide itself.
* Change API of `compact_range` to be compatible with the change of
  rocksdb.
* No need for the `get_ob` methods on PyCache.
* Add `row_cache` to options.
* Document the new `row_cache` option.
* Update the versions (python, rocksdb) `pyrocksdb` 0.4 was tested with.
* Mention in the changelog that this version is avaialable on pypi.

Version 0.4
-----------
This version works with RocksDB v3.12.

* Added :py:func:`repair_db`.
* Added :py:meth:`rocksdb.Options.row_cache`
* Publish to pypi.

Backward Incompatible Changes:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Changed API of :py:meth:`rocksdb.DB.compact_range`.

    * Only allow keyword arguments.
    * Changed ``reduce_level`` to ``change_level``.
    * Add new argument called ``bottommost_level_compaction``.


Version 0.3
-----------
This version works with RocksDB version v3.11.

Backward Incompatible Changes:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Prefix Seeks:**

According to this page https://github.com/facebook/rocksdb/wiki/Prefix-Seek-API-Changes,
all the prefix related parameters on ``ReadOptions`` are removed.
Rocksdb realizes now if ``Options.prefix_extractor`` is set and uses then
prefix-seeks automatically. This means the following changes on pyrocksdb.

* DB.iterkeys, DB.itervalues, DB.iteritems have *no* ``prefix`` parameter anymore.
* DB.get, DB.multi_get, DB.key_may_exist, DB.iterkeys, DB.itervalues, DB.iteritems
  have *no* ``prefix_seek`` parameter anymore.

Which means all the iterators walk now always to the *end* of the database.
So if you need to stay within a prefix, write your own code to ensure that.
For DB.iterkeys and DB.iteritems ``itertools.takewhile`` is a possible solution. ::

    from itertools import takewhile

    it = self.db.iterkeys()
    it.seek(b'00002')
    print list(takewhile(lambda key: key.startswith(b'00002'), it))

    it = self.db.iteritems()
    it.seek(b'00002')
    print dict(takewhile(lambda item: item[0].startswith(b'00002'), it))

**SST Table Builders:**

* Removed ``NewTotalOrderPlainTableFactory``, because rocksdb drops it too.

**Changed Options:**

In newer versions of rocksdb a bunch of options were moved or removed.

* Rename ``bloom_bits_per_prefix`` of :py:class:`rocksdb.PlainTableFactory` to ``bloom_bits_per_key``
* Removed ``Options.db_stats_log_interval``.
* Removed ``Options.disable_seek_compaction``
* Moved ``Options.no_block_cache`` to ``BlockBasedTableFactory``
* Moved ``Options.block_size`` to ``BlockBasedTableFactory``
* Moved ``Options.block_size_deviation`` to ``BlockBasedTableFactory``
* Moved ``Options.block_restart_interval`` to ``BlockBasedTableFactory``
* Moved ``Options.whole_key_filtering`` to ``BlockBasedTableFactory``
* Removed ``Options.table_cache_remove_scan_count_limit``
* Removed rm_scan_count_limit from ``LRUCache``


New:
~~~~
* Make CompactRange available: :py:meth:`rocksdb.DB.compact_range`
* Add init options to :py:class:`rocksdb.BlockBasedTableFactory`
* Add more option to :py:class:`rocksdb.PlainTableFactory`
* Add :py:class:`rocksdb.WriteBatchIterator`
* add :py:attr:`rocksdb.CompressionType.lz4_compression`
* add :py:attr:`rocksdb.CompressionType.lz4hc_compression`


Version 0.2
-----------

This version works with RocksDB version 2.8.fb. Now you have access to the more
advanced options of rocksdb. Like changing the memtable or SST representation.
It is also possible now to enable *Universal Style Compaction*.

* Fixed `issue 3 <https://github.com/stephan-hof/pyrocksdb/pull/3>`_.
  Which fixed the change of prefix_extractor from raw-pointer to smart-pointer.

* Support the new :py:attr:`rocksdb.Options.verify_checksums_in_compaction` option.

* Add :py:attr:`rocksdb.Options.table_factory` option. So you could use the new
  'PlainTableFactories' which are optimized for in-memory-databases.

  * https://github.com/facebook/rocksdb/wiki/PlainTable-Format
  * https://github.com/facebook/rocksdb/wiki/How-to-persist-in-memory-RocksDB-database%3F

* Add :py:attr:`rocksdb.Options.memtable_factory` option.

* Add options :py:attr:`rocksdb.Options.compaction_style` and
  :py:attr:`rocksdb.Options.compaction_options_universal` to change the
  compaction style.

* Update documentation to the new default values

  * allow_mmap_reads=true
  * allow_mmap_writes=false
  * max_background_flushes=1
  * max_open_files=5000
  * paranoid_checks=true
  * disable_seek_compaction=true
  * level0_stop_writes_trigger=24
  * level0_slowdown_writes_trigger=20

* Document new property names for :py:meth:`rocksdb.DB.get_property`.

Version 0.1
-----------

Initial version. Works with rocksdb version 2.7.fb.

 .. _`Martina Ferrari`: https://github.com/NightTsarina/
 .. _`Andrey Martyanov`: https://github.com/martyanov/
 .. _`@iFA88`: https://github.com/iFA88/
 .. _`Ming-Hsuan-Tu`: https://github.com/twmht/
 .. _`Stephan Hofmockel`: https://github.com/stephan-hof/
