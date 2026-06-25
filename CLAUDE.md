# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cython bindings for the RocksDB C++ library. Published to PyPI as **`rocksdb-ng`**
but imported as `rocksdb` (the plain `rocksdb` PyPI name belongs to the upstream
project this is forked from). Supports RocksDB **8.x–10.x** and Python **3.11–3.14**.
This is a fork consolidating the abandoned `pyrocksdb` / `python-rocksdb` forks.

## Prerequisites

The build needs a **system RocksDB** at compile time. It is located via
`pkg-config` (`rocksdb.pc`, from `librocksdb-dev`); if that fails, `setup.py`
falls back to the `INCLUDE_PATH` / `LIBRARY_PATH` env vars, then to the
compiler's default search paths. Only `-lrocksdb` is linked — a shared
`librocksdb.so` pulls in its own codec deps (snappy/bz2/z/lz4/zstd). If you link
a *static* `librocksdb.a`, you must re-add those codecs in `setup.py`.

`setup.py` enforces the supported RocksDB range (`MIN_ROCKSDB` /
`MAX_TESTED_ROCKSDB_MAJOR`, currently 8.0–10.x): it detects the version via
`pkg-config` / `<rocksdb/version.h>` and aborts early with a clear message on
anything outside the range — both anything older (below the floor, headers like
`<rocksdb/utilities/backup_engine.h>` are missing and the build would otherwise
fail opaquely mid-compile) and a newer-than-tested major (unverified, and may
rely on changed APIs). The force-included `rocksdb/cpp/version_check.hpp` is a
compile-time backstop for when the version can't be detected before compiling
(RocksDB on the default search path); keep its bounds in sync with `MIN_ROCKSDB` /
`MAX_TESTED_ROCKSDB_MAJOR`.

## Common commands

```bash
# Build + install (use pip, NOT `python setup.py ...`, so the Cython/pkgconfig
# build deps from pyproject.toml get installed):
pip install '.[test]'

# Rebuild the extension in-place after editing .pyx/.pxd (needs Cython present):
python setup.py build_ext --inplace      # writes rocksdb/_rocksdb.{cpp,so}

# Run the whole test suite (see "Test import convention" below for why --pyargs):
pytest --pyargs rocksdb

# Single test module / single test:
pytest --pyargs rocksdb.tests.test_db
pytest --pyargs rocksdb.tests.test_db -k test_put_get

# Full matrix via tox (py311–py314, plus a docs env):
tox -e py312
tox -e docs

# Build the docs directly:
sphinx-build -W -b html docs docs/_build/html   # -W = warnings are errors

# Build the reproducible PyPI sdist (the ONLY supported publish artifact):
./contrib/sdist/build.sh            # packages HEAD
./contrib/sdist/build.sh v2.0.0     # packages a tag
```

### Test import convention

Tests run against the **installed** package, not the source tree, hence the
`--pyargs rocksdb` form everywhere (CI, tox, `[pytest] addopts` in `tox.ini`).
Running plain `pytest rocksdb/tests/...` from the repo root imports the source
`rocksdb/` directory, which only works if the compiled `.so` is present in-tree
(e.g. after `build_ext --inplace`) — otherwise it fails to find the extension.
Prefer the `--pyargs` form to test what users actually get.

## Architecture

The entire binding compiles to a **single extension module**, `rocksdb._rocksdb`,
from `rocksdb/_rocksdb.pyx` (~2800 lines — all the wrapper classes live here:
`DB`, `Options`, `WriteBatch`, iterators, `TransactionDB`, `BackupEngine`, table
factories, caches, etc.). `rocksdb/__init__.py` is just `from ._rocksdb import *`.

Three layers cooperate:

1. **`.pxd` declaration files** (`db.pxd`, `options.pxd`, `iterator.pxd`, …) —
   one per RocksDB subsystem. Each is a Cython transcription of the corresponding
   C++ header via `cdef extern from "rocksdb/<header>.h" namespace "rocksdb"`.
   These declare the RocksDB types to Cython; they contain no logic. When wrapping
   a new RocksDB API, add/extend the matching `.pxd` first, then use it in the `.pyx`.

2. **`rocksdb/cpp/*.hpp` C++ shims** — hand-written classes that subclass RocksDB
   abstract interfaces (`Comparator`, `MergeOperator`, `SliceTransform`,
   memtable factories, a `WriteBatch` handler) and **trampoline their virtual
   methods back into Python callbacks**. This is how a user-defined Python
   comparator/merge-operator works: the `.pyx` constructs the wrapper with a C
   function pointer + a `void*` context (the Python object), and the wrapper
   invokes it from inside RocksDB. These are pulled in via
   `cdef extern from "cpp/...hpp" namespace "py_rocks"`.

3. **`.pyx` glue** — converts between Python `bytes` and RocksDB `Slice`/
   `std::string` (`bytes_to_string`, `bytes_to_slice`, `slice_to_bytes`, …),
   and translates every RocksDB `Status` into the right exception via
   `check_status()`. The exception classes themselves live in
   `rocksdb/errors.py` (`Error` → `NotFound`, `Corruption`, `InvalidArgument`,
   `RocksIOError`, …); `check_status` is the single mapping point.

**Pure-Python support modules:** `rocksdb/interfaces.py` (ABCs users subclass:
`Comparator`, `MergeOperator`, `AssociativeMergeOperator`, `SliceTransform`),
`rocksdb/errors.py` (the exception hierarchy), `rocksdb/merge_operators.py`
(ready-made `UintAddOperator` / `StringAppendOperator`).

## Critical constraints

- **Compiles as C++20** (`-std=c++20` in `setup.py`) — required by RocksDB 10.x
  headers (defaulted `operator==`, `using enum`).

- **Requires Cython `>=3.2.5,<4`** (`pyproject.toml` build-system requires). The
  old constraint to *also* stay compilable under Debian trixie's system Cython
  3.0.11 has been dropped — the project is not a Debian-packaging target and the
  `debian.yml` CI job now builds with pip's build isolation (a modern Cython from
  PyPI). You are therefore free to use Cython 3.1+/3.2+ primitives (e.g.
  `cython.pymutex`, `cython.critical_section`, the `freethreading_compatible`
  directive). The existing `bytes_to_string(...)` calls in the `.pyx` callbacks
  are still correct and need not be undone.

- **`rocksdb-ng` ships the same top-level `rocksdb` import package** as the
  upstream `rocksdb` distribution, so the two cannot be co-installed. Reflect
  this in any install docs.

## CI workflows (`.github/workflows/`)

- `build.yml` — builds RocksDB 9.10 & 10.10 from source (cached), then builds &
  tests the binding across Python 3.11–3.14.
- `debian.yml` — builds & tests against Debian trixie's distro-packaged
  `librocksdb-dev` (the `apt install librocksdb-dev` + `pip install` path), using
  a modern isolated Cython from PyPI.
- `docs.yml` — builds the Sphinx docs (`-W`) and deploys to GitHub Pages from `main`.
- `sdist.yml` — verifies the reproducible sdist is byte-identical under Docker and
  rootless Podman. **CI never publishes**; releases are uploaded manually
  (see `contrib/sdist/README.md`).

Push to a `force_ci/all/**` (or per-workflow `force_ci/<name>/**`) branch to force
workflows to run outside of a PR.
