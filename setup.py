#!/usr/bin/env python3

import os
import platform
import re
import sys

import pkgconfig
from Cython.Build import cythonize
from setuptools import Extension, setup

HERE = os.path.dirname(os.path.abspath(__file__))

# Supported RocksDB C++ API range. Below MIN_ROCKSDB the headers lack APIs this
# binding needs (e.g. <rocksdb/utilities/backup_engine.h>, added in 7.0) and the
# build explodes mid-compile with an opaque error; at or above it the binding is
# tested through the 10.x series. Newer-than-tested majors usually work but are
# unverified, so we only warn. Keep MIN in sync with rocksdb/cpp/version_check.hpp.
MIN_ROCKSDB = (8, 0, 0)
MAX_TESTED_ROCKSDB_MAJOR = 10


def parse_rocksdb_version(text):
    """Return a (major, minor, patch) tuple parsed from a version string (e.g.
    ``pkg-config --modversion`` output) or the contents of
    <rocksdb/version.h>, or None if no version can be found."""
    if not text:
        return None
    # The ROCKSDB_MAJOR/MINOR/PATCH macros take priority: a version.h also
    # contains other "N.N" text (license/SPDX lines) that the plain-version
    # regex below could otherwise misread when handed the whole header.
    macros = {}
    for name in ('MAJOR', 'MINOR', 'PATCH'):
        found = re.search(r'#\s*define\s+ROCKSDB_%s\s+(\d+)' % name, text)
        if found:
            macros[name] = int(found.group(1))
    if 'MAJOR' in macros and 'MINOR' in macros:
        return (macros['MAJOR'], macros['MINOR'], macros.get('PATCH', 0))
    # Otherwise a plain "8.9.1"-style string (e.g. `pkg-config --modversion`).
    match = re.match(r'\s*(\d+)\.(\d+)(?:\.(\d+))?', text)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    return None


def detect_rocksdb_version(include_dirs):
    """Best-effort detection of the RocksDB version being built against.

    Tries pkg-config first, then <rocksdb/version.h> on the include path
    (pkg-config dirs, then INCLUDE_PATH, then the usual system locations).
    Returns a (major, minor, patch) tuple, or None when it cannot be
    determined -- in which case the compile-time guard in
    rocksdb/cpp/version_check.hpp is the backstop."""
    try:
        version = parse_rocksdb_version(pkgconfig.modversion('rocksdb'))
        if version:
            return version
    except Exception:
        # pkg-config missing, no rocksdb.pc, etc. -- fall through to headers.
        pass

    search = list(include_dirs or [])
    env_include = os.environ.get('INCLUDE_PATH')
    if env_include:
        search += env_include.split(os.pathsep)
    search += ['/usr/include', '/usr/local/include']

    for include_dir in search:
        header = os.path.join(include_dir, 'rocksdb', 'version.h')
        try:
            with open(header) as handle:
                version = parse_rocksdb_version(handle.read())
        except OSError:
            continue
        if version:
            return version
    return None


def enforce_rocksdb_version(version):
    """Abort the build with a clear message on an unsupported RocksDB, or warn
    on an untested-but-newer one. A None version is left to the compile-time
    guard in rocksdb/cpp/version_check.hpp."""
    if version is None:
        return
    found = '.'.join(map(str, version))
    minimum = '.'.join(map(str, MIN_ROCKSDB))
    supported_range = '%d.x-%d.x' % (MIN_ROCKSDB[0], MAX_TESTED_ROCKSDB_MAJOR)
    if version < MIN_ROCKSDB:
        sys.exit(
            'ERROR: rocksdb-ng requires RocksDB >= %s, but found %s.\n'
            'RocksDB %s lacks C++ APIs this binding needs, so the build would '
            'otherwise fail later in an opaque way. Install librocksdb-dev %s, '
            'or point INCLUDE_PATH/LIBRARY_PATH at a supported RocksDB.'
            % (minimum, found, found, supported_range)
        )
    if version[0] > MAX_TESTED_ROCKSDB_MAJOR:
        sys.stderr.write(
            'WARNING: building rocksdb-ng against RocksDB %s, which is newer '
            'than the tested range (%s). It will probably work, but is '
            'unverified -- please report success or failure.\n'
            % (found, supported_range)
        )


def main():
    extra_compile_args = [
        # RocksDB 10.x headers use C++20 features (defaulted `operator==`, `using
        # enum`), so the binding must be compiled as C++20. C++20 also compiles the
        # 8.x/9.x headers fine, so this is safe across the supported RocksDB range.
        '-std=c++20',
        '-O3',
        '-Wall',
        '-Wextra',
        '-Wconversion',
        '-fno-strict-aliasing',
        '-fno-rtti',
    ]

    if platform.system() == 'Darwin':
        extra_compile_args += ['-mmacosx-version-min=10.13', '-stdlib=libc++']

    try:
        ext_args = pkgconfig.parse('rocksdb')
    except pkgconfig.PackageNotFoundError:
        include_path = os.environ.get('INCLUDE_PATH')
        library_path = os.environ.get('LIBRARY_PATH')

        if not include_path and not library_path:
            sys.stderr.write(
                'WARNING: pkg-config could not find a `rocksdb` package and neither '
                'INCLUDE_PATH nor LIBRARY_PATH is set. Falling back to the compiler\'s '
                'default search paths. If the build fails to find <rocksdb/db.h> or '
                '-lrocksdb, install librocksdb-dev (providing rocksdb.pc) or set '
                'INCLUDE_PATH/LIBRARY_PATH to your RocksDB installation.\n'
            )

        ext_args = {
            'include_dirs': include_path.split(os.pathsep) if include_path else [],
            'library_dirs': library_path.split(os.pathsep) if library_path else [],
            # Only link librocksdb. A shared librocksdb.so already declares its own
            # compression-library dependencies (snappy/bz2/z/lz4/zstd), so listing
            # them here just risks a spurious `cannot find -l<lib>` when a `-dev`
            # package is absent or RocksDB was built without that codec. If you link
            # a *static* librocksdb.a, re-add the codecs you compiled it with (or,
            # preferably, install rocksdb.pc so the pkgconfig path above is used).
            'libraries': ['rocksdb'],
        }

    # Fail fast on an unsupported RocksDB instead of silently building and then
    # breaking later in an opaque way.
    enforce_rocksdb_version(detect_rocksdb_version(ext_args.get('include_dirs')))

    # Compile-time backstop: force-include a header that #errors out on an
    # unsupported <rocksdb/version.h>. This covers the case where the version
    # could not be detected above (RocksDB on the compiler's default search
    # path), turning a wall of missing-symbol errors into one clear message.
    extra_compile_args += [
        '-include', os.path.join(HERE, 'rocksdb', 'cpp', 'version_check.hpp'),
    ]

    rocksdb_extension = Extension(
        'rocksdb._rocksdb',
        [
            'rocksdb/_rocksdb.pyx',
        ],
        extra_compile_args=extra_compile_args,
        language='c++',
        **ext_args,
    )

    setup(
        ext_modules=cythonize([rocksdb_extension]),
    )


if __name__ == '__main__':
    main()
