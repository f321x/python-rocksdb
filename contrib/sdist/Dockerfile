# Builder image for the reproducible python-rocksdb sdist.
#
# Pinned by digest so the build environment never drifts. debian:bookworm-slim
# ships Python 3.11; the exact Python and toolchain versions are what make the
# Cython-generated `_rocksdb.cpp` -- and thus the tarball -- byte-for-byte
# reproducible. To bump: pull the new tag and replace the digest below.
FROM debian@sha256:0104b334637a5f19aa9c983a91b54c89887c0984081f2068983107a6f6c21eeb

ENV LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1

# git: the sdist is built from a `git archive` tree extracted on the host.
# pkg-config: setup.py calls pkgconfig.parse('rocksdb'); with no RocksDB present
# it falls back cleanly, but the pkg-config binary must exist for that call.
RUN apt-get update -q && \
    apt-get install -qy --no-install-recommends \
        ca-certificates \
        git \
        pkg-config \
        python3 \
        python3-venv \
        && \
    rm -rf /var/lib/apt/lists/*

# Pinned + hashed build toolchain in a venv. Debian's system Python is PEP 668
# "externally managed", so we never pip-install into it. --require-hashes makes
# the image build itself reproducible and tamper-evident.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv "$VIRTUAL_ENV" && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# Build as a non-root user for hygiene. The artifact is copied out of the
# container with `<engine> cp`, so it lands owned by the invoking host user
# regardless of this UID -- which is what stops rootless Podman from leaving
# behind sub-UID-owned files that the host user cannot delete.
RUN useradd --create-home --shell /bin/bash --uid 1000 user
COPY make_sdist.sh /usr/local/bin/make_sdist.sh
RUN chmod +x /usr/local/bin/make_sdist.sh

USER user
# NOTE: this home/WORKDIR is a contract. make_sdist.sh writes the tarball to
# $HOME/wspace/dist and build.sh copies it out from the hardcoded CTR_OUT
# (/home/user/wspace/dist). Changing the user/home/WORKDIR here means updating
# both of those too.
WORKDIR /home/user/wspace
CMD ["bash", "/usr/local/bin/make_sdist.sh"]
