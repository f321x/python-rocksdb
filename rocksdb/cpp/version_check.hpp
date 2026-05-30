#pragma once

// Compile-time bounds for the RocksDB C++ API this binding targets.
//
// This is the backstop for the friendly, early check in setup.py. setup.py
// detects the RocksDB version via pkg-config / <rocksdb/version.h> and aborts
// with a clear message before compiling -- but it cannot see the version when
// RocksDB lives on the compiler's default search path (no pkg-config, no
// INCLUDE_PATH). setup.py force-includes this header (-include) so that case
// still fails with one clear message instead of a wall of "missing symbol" /
// "no such file" errors deep in the build for a too-old RocksDB (e.g.
// <rocksdb/utilities/backup_engine.h>, which only exists from RocksDB 7.0
// onward), or a silently-built, untested module for a too-new one.
//
// Keep both bounds in sync with MIN_ROCKSDB / MAX_TESTED_ROCKSDB_MAJOR in
// setup.py.

#include "rocksdb/version.h"

#if !defined(ROCKSDB_MAJOR) || !defined(ROCKSDB_MINOR)
#error "rocksdb-ng: <rocksdb/version.h> did not define ROCKSDB_MAJOR/ROCKSDB_MINOR; cannot verify the RocksDB version. Install a supported librocksdb-dev (8.x-10.x)."
#elif ROCKSDB_MAJOR < 8
#error "rocksdb-ng requires RocksDB >= 8.0.0, but an older major version was found. Install librocksdb-dev 8.x-10.x, or point INCLUDE_PATH/LIBRARY_PATH at a supported RocksDB."
#elif ROCKSDB_MAJOR > 10
#error "rocksdb-ng supports RocksDB up to the 10.x series, but a newer major version was found. It is untested and may rely on changed C++ APIs, so the build is refused. Install librocksdb-dev 8.x-10.x, or point INCLUDE_PATH/LIBRARY_PATH at a supported RocksDB."
#endif
