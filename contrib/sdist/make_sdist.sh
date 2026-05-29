#!/usr/bin/env bash
#
# Runs INSIDE the builder container (see Dockerfile / build.sh). Builds the
# python-rocksdb sdist from the read-only source tree mounted at /src and
# normalises the resulting tarball so it is byte-for-byte reproducible.
#
# Inputs (from the environment):
#   SOURCE_DATE_EPOCH   Unix timestamp used for every archive mtime. build.sh
#                       sets it to the commit time of the ref being built.
set -euo pipefail

SRC=/src
WORK="$HOME/wspace"
PKG="$WORK/pkg"            # writable copy of the source we actually build from
RAW="$WORK/raw"           # setuptools' (non-reproducible) tarball lands here
EXTRACT="$WORK/extract"   # unpacked tarball, before re-archiving deterministically
OUT="$WORK/dist"          # normalised tarball; build.sh copies this out

: "${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH must be set by the caller}"
export SOURCE_DATE_EPOCH
export PYTHONHASHSEED=0    # guard against dict-ordering nondeterminism in tooling
umask 022                 # deterministic file-creation mode (the --mode= on the
                          # final tar below is the load-bearing normalization)

rm -rf "$PKG" "$RAW" "$EXTRACT" "$OUT"
mkdir -p "$PKG" "$RAW" "$EXTRACT" "$OUT"

# setuptools writes *.egg-info into the project tree, so we keep /src read-only
# and build from a writable copy instead.
cp -a "$SRC"/. "$PKG"/
cd "$PKG"

# Build with the pinned, pre-installed toolchain. --no-isolation means no network
# and no fresh dependency resolution: the Cython -> _rocksdb.cpp transpilation is
# fully deterministic.
python -m build --sdist --no-isolation --outdir "$RAW"

# setuptools' sdist tarball is not reproducible on its own: per-file mtimes, the
# gzip header timestamp, file ownership, and member ordering all vary. Unpack it
# and re-create the archive deterministically.
RAW_TARBALL="$(find "$RAW" -maxdepth 1 -type f -name '*.tar.gz' -printf '%f\n')"
if [ -z "$RAW_TARBALL" ]; then
    echo "ERROR: no sdist produced in $RAW" >&2
    exit 1
fi
DISTNAME="${RAW_TARBALL%.tar.gz}"   # e.g. rocksdb_ng-2.0.0 (PEP 625 underscore)

tar -xzf "$RAW/$RAW_TARBALL" -C "$EXTRACT"

# Re-archive deterministically:
#   --sort=name        stable member ordering
#   --mtime            every entry stamped with the commit time
#   --owner/--group 0  + --numeric-owner: never leak the build UID/GID
#   --mode             normalize permission bits (files 0644, dirs 0755) so the
#                      builder's umask never leaks into the hashed bytes -- the
#                      source modes otherwise flow through from the host's
#                      `git archive | tar -x` and vary with umask across machines
#   --format=gnu       stable header format across tar versions in the pinned image
#   gzip -n            drop gzip's own name/timestamp header
tar --create \
    --format=gnu \
    --sort=name \
    --numeric-owner --owner=0 --group=0 \
    --mode='go-w,a+rX,u+w' \
    --mtime="@$SOURCE_DATE_EPOCH" \
    --directory="$EXTRACT" \
    "$DISTNAME" \
  | gzip -n > "$OUT/$RAW_TARBALL"

echo "==> reproducible sdist written to dist/$RAW_TARBALL"
( cd "$OUT" && sha256sum "$RAW_TARBALL" )
