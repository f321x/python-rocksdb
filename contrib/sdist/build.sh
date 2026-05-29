#!/usr/bin/env bash
#
# Build a reproducible python-rocksdb source distribution (sdist) inside a
# pinned container, so the tarball published to PyPI is byte-for-byte
# reproducible from a given commit.
#
# Usage:
#   ./contrib/sdist/build.sh [GIT_REF]
#
#   GIT_REF   commit/tag/branch to package (default: HEAD). The sdist is built
#             from a clean `git archive` of this ref, so only committed files
#             are packaged -- uncommitted changes and build artefacts never leak
#             in, and the same ref always yields the same bytes.
#
# Environment:
#   CONTAINER_ENGINE   container CLI to use (default: docker). Set to `podman`
#                      to build with rootless Podman. Only flags common to both
#                      Docker and Podman are used; on SELinux hosts the `:z`
#                      mount label relabels the bind mount automatically, and the
#                      artifact is copied out with `<engine> cp` so it is owned by
#                      the invoking user under both rootful Docker and rootless
#                      Podman. The same command therefore works unchanged for a
#                      `docker` that is really the podman-docker wrapper.
#   SDIST_NOCACHE      if non-empty, rebuild the builder image from scratch
#                      (`--no-cache --pull`).
#
# Output: ./dist/rocksdb_ng-<version>.tar.gz (owned by the invoking user). The
#         PyPI project is `rocksdb-ng`; PEP 625 renders that as `rocksdb_ng` in
#         the sdist filename. The import package is still `rocksdb`.
set -euo pipefail

ENGINE="${CONTAINER_ENGINE:-docker}"
GIT_REF="${1:-HEAD}"

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROJECT_ROOT="$(cd "$HERE/../.." && pwd)"
DISTDIR="$PROJECT_ROOT/dist"
IMAGE_TAG="python-rocksdb-sdist-builder"
CONTAINER_NAME="python-rocksdb-sdist-$$"   # PID keeps concurrent runs distinct
# In-container path where make_sdist.sh writes the normalized tarball. MUST stay
# in sync with the Dockerfile's WORKDIR / user home and make_sdist.sh's $OUT.
CTR_OUT="/home/user/wspace/dist"

if ! command -v "$ENGINE" >/dev/null 2>&1; then
    echo "ERROR: container engine '$ENGINE' not found on PATH." >&2
    exit 1
fi

# Reproducibility knob: stamp the archive with the commit's own timestamp, so the
# same commit always produces the same mtimes (and therefore the same bytes).
SOURCE_DATE_EPOCH="$(git -C "$PROJECT_ROOT" log -1 --format=%ct "$GIT_REF")"
export SOURCE_DATE_EPOCH

echo "==> engine             : $ENGINE"
echo "==> ref                : $GIT_REF"
echo "==> SOURCE_DATE_EPOCH  : $SOURCE_DATE_EPOCH"

# 1. Export a clean source tree (committed files only) from the requested ref.
SRCDIR="$(mktemp -d)"
cleanup() {
    rm -rf "$SRCDIR"
    "$ENGINE" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
git -C "$PROJECT_ROOT" archive --format=tar "$GIT_REF" | tar -x -C "$SRCDIR"

# 2. Build the pinned builder image (build logic + toolchain baked in from HERE,
#    decoupled from the packaged source).
build_flags=()
if [ -n "${SDIST_NOCACHE:-}" ]; then
    build_flags+=(--no-cache --pull)
fi
# ${arr[@]+"${arr[@]}"} expands to nothing safely even on bash < 4.4 under set -u.
"$ENGINE" build ${build_flags[@]+"${build_flags[@]}"} -t "$IMAGE_TAG" "$HERE"

# 3. Build offline. Source is bind-mounted read-only (`ro` so the container can't
#    touch it; `z` relabels for SELinux). The artifact is produced inside the
#    container and copied out in step 4 -- never via a writable bind mount, which
#    is what avoids the root/sub-UID ownership traps under rootless Podman.
"$ENGINE" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
"$ENGINE" run \
    --name "$CONTAINER_NAME" \
    --network=none \
    -e SOURCE_DATE_EPOCH \
    -v "$SRCDIR":/src:ro,z \
    "$IMAGE_TAG"

# 4. Copy the artifact out as a tar stream and extract it host-side. Extracting
#    as the invoking user makes the tarball owned by that user under BOTH rootful
#    Docker and rootless Podman, independent of the container UID and of either
#    engine's `cp` chown behaviour. (A writable bind mount would instead leave
#    sub-UID-owned files under rootless Podman.) dist/ is recreated so a stale
#    tarball from a previous/other version can't linger beside the new one.
rm -rf "$DISTDIR" && mkdir -p "$DISTDIR"
"$ENGINE" cp "$CONTAINER_NAME:$CTR_OUT/." - | tar -x -C "$DISTDIR"

echo "==> done. artifacts in $DISTDIR:"
ls -l "$DISTDIR"/*.tar.gz
( cd "$DISTDIR" && sha256sum ./*.tar.gz )
