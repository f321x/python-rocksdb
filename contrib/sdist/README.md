# Reproducible source distribution (sdist)

This is the **only** supported way to build and publish the package to PyPI,
where it is published under the distribution name **`rocksdb-ng`** (the plain
`rocksdb` name is taken by the upstream project this is forked from; the *import*
name is unchanged — users still `import rocksdb`). It produces a *reproducible*
source tarball: building the same git commit twice — on any machine, with Docker
or rootless Podman — yields a byte-for-byte identical
`dist/rocksdb_ng-<version>.tar.gz`. (Per PEP 625 the `-ng` in the project name
becomes `_ng` in the filename.)

Why this matters here: our sdist embeds the Cython-generated
`rocksdb/_rocksdb.cpp`, so the tarball's bytes depend on the exact Cython
version. The build therefore runs in a digest-pinned container with a pinned,
hashed build toolchain (`requirements.txt`), and the resulting tarball is
re-archived deterministically (sorted members, fixed mtimes, zeroed ownership,
no gzip timestamp).

## Build

```sh
./contrib/sdist/build.sh            # packages HEAD
./contrib/sdist/build.sh v2.0.0     # packages a specific tag/commit
```

The tarball is written to `./dist/` owned by your user.

### Docker or rootless Podman (Fedora / SELinux)

The script invokes the `docker` CLI by default and uses only flags that Docker
and Podman share, so it works as-is when `docker` is the
[`podman-docker`](https://packages.fedoraproject.org/pkgs/podman/podman-docker/)
wrapper. To call Podman directly:

```sh
CONTAINER_ENGINE=podman ./contrib/sdist/build.sh
```

The source is bind-mounted **read-only** with the `:z` SELinux label, and the
artifact is streamed out of the container (`<engine> cp … -`) and extracted on
the host — never written through a writable bind mount. Extracting host-side
means the tarball is owned by *your* user (not a container sub-UID) under both
rootful Docker and rootless Podman, so there are no SELinux-relabel or
sub-UID-ownership pitfalls. CI asserts this ownership on the rootless-Podman leg.

### Other knobs

- `SDIST_NOCACHE=1` — rebuild the builder image from scratch (`--no-cache --pull`).

## Verify reproducibility

```sh
./contrib/sdist/build.sh && sha256sum dist/*.tar.gz
mv dist dist.1
./contrib/sdist/build.sh && sha256sum dist/*.tar.gz   # same hash
```

CI runs exactly this check on every pull request, push to `main`, and `v*` tag,
under **both** Docker and rootless Podman, and asserts the artifact is owned by
the invoking user. See `.github/workflows/sdist.yml`. CI **never publishes** —
see "Publishing" below.

> **Note:** GitHub's Ubuntu runners are not SELinux-enforcing, so CI validates
> the rootless-Podman code path but cannot exercise SELinux relabeling itself.
> The `:z` labels are no-ops there and are validated on a real SELinux host.

## Publishing to PyPI (manual)

Releases are uploaded **manually** by a maintainer; there is no automated publish
job. Build the reproducible sdist for the release commit and upload it:

```sh
./contrib/sdist/build.sh v2.0.0          # package the tagged commit
pipx run twine check --strict dist/*.tar.gz
pipx run twine upload dist/rocksdb_ng-*.tar.gz   # note: underscore (PEP 625)
```

Alternatively, download the `sdist-docker` artifact from that tag's CI run — it
has already been reproducibility- and `twine check`-verified — and
`twine upload` it. Either way, PyPI versions are immutable, so verify the
`twine check` passes before uploading.

## Files

| File | Role |
|------|------|
| `build.sh` | Host orchestrator: `git archive` → build image → run → copy artifact out. |
| `make_sdist.sh` | Runs in the container: `python -m build --sdist` + deterministic re-archive. |
| `Dockerfile` | Digest-pinned Debian + venv with the pinned toolchain. |
| `requirements.txt` | Pinned, hashed build toolchain (`pip-compile --generate-hashes`). |

## Bumping the toolchain or base image

- **Toolchain:** regenerate `requirements.txt` with the command in its header,
  keeping the pins within the `[build-system].requires` bounds in
  `../../pyproject.toml`. A new Cython version changes the generated C++ and thus
  the tarball hash — expected, and that is exactly why the toolchain is pinned.
- **Base image:** pull the new `debian:bookworm-slim`, copy its
  `@sha256:` digest into the `Dockerfile`, and regenerate `requirements.txt`
  inside it so the hashes still match its Python.
