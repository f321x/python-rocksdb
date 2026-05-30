# Reproducible source distribution (sdist)

This is the **only** supported way to build and publish the package to PyPI,
where it is published under the distribution name **`rocksdb-ng`**.
It produces a *reproducible* source tarball.

## Build

```sh
./contrib/sdist/build.sh            # packages HEAD
./contrib/sdist/build.sh v2.0.0     # packages a specific tag/commit
```

The tarball is written to `./dist/` owned by your user.

### Dependencies

Docker or Podman have to be installed on the system.
Podman either needs the `docker-podman` wrapper or needs to be passed explicitly
as `CONTAINER_ENGINE=podman` environment variable.

### Other knobs

- `SDIST_NOCACHE=1` — rebuild the builder image from scratch (`--no-cache --pull`).

## Verify reproducibility

```sh
./contrib/sdist/build.sh && sha256sum dist/*.tar.gz
mv dist dist.1
./contrib/sdist/build.sh && sha256sum dist/*.tar.gz   # same hash
```

## Publishing to PyPI (manual)

Build the reproducible sdist for the release commit and upload it:

```sh
./contrib/sdist/build.sh v2.0.0
twine check --strict dist/*.tar.gz
twine upload dist/rocksdb_ng-*.tar.gz
```

## Bumping the toolchain or base image

- **Toolchain:** regenerate `requirements.txt` with the command in its header,
  keeping the pins within the `[build-system].requires` bounds in
  `../../pyproject.toml`. 
- **Base image:** pull the new `debian:bookworm-slim`, copy its
  `@sha256:` digest into the `Dockerfile`, and regenerate `requirements.txt`
  inside it so the hashes still match its Python.
