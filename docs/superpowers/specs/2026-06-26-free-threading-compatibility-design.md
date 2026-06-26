# Free-threading compatibility for `rocksdb-ng` — design

Date: 2026-06-26
Status: approved

## Goal

Make `rocksdb-ng` import and run correctly on a free-threaded ("nogil") CPython
build, declare that support honestly (`Py_mod_gil = Py_MOD_GIL_NOT_USED`), and
cover it in CI — including a ThreadSanitizer leg for true data-race detection.

Today the module carries **zero explicit synchronization**. Correctness rests
entirely on two implicit mechanisms:

1. the GIL serializing every Python-level mutation, and
2. a strong-reference ownership chain (`DB → Options → Py*Wrapper → ob`) plus
   teardown ordering in `close()`.

The module does not declare `freethreading_compatible`, so on a free-threaded
interpreter importing it silently **re-enables the GIL** (with a warning). Every
race below is therefore currently *masked*. The moment we advertise FT support
the masking disappears, so the synchronization work must land before (or with)
the directive.

## Scope decisions (made during brainstorming)

- **Lifecycle-only locking.** A per-instance lock protects object *lifecycle*
  and column-family management. The data path (`get`/`put`/`delete`/`write`/
  `multi_get`/`key_may_exist`/iterator creation) stays lock-free — RocksDB is
  internally thread-safe. Documented contract: *a DB must not be closed while
  other threads are still operating on it.*
- **Target runtime:** CPython **3.14t** only (free-threaded).
- **Race detection:** a PYTHON_GIL=0 stress suite **plus** a ThreadSanitizer CI
  leg (TSan-built interpreter + TSan-built RocksDB).
- **Iterators and `WriteBatch`** are documented as single-thread-owned (not
  shareable across threads); only their lifecycle vs DB teardown is synchronized.
- **Custom comparators / merge-operators / slice-transforms** must be internally
  thread-safe; the binding does not serialize user callbacks (RocksDB runs them
  concurrently across subcompactions). We do add refcount-based lifetime safety.

No Cython version bump is needed: the floor is already `Cython>=3.2.5,<4`, and
`freethreading_compatible`, `cython.pymutex`, and `cython.critical_section` are
all available from Cython 3.1+.

## Architecture

### Two synchronization primitives, two jobs

| Primitive | Where | Why this one |
|---|---|---|
| `cython.pymutex` (per-`DB`/`TransactionDB` instance) | object lifecycle + cf-list RMW + child teardown | Must be **held across `with nogil:` C++ calls** (e.g. `Close()`). `critical_section` is released by a `nogil` block, so it cannot protect a region that drops the thread-state. |
| `cython.critical_section(self)` | `Options.in_use` check-then-set; `_ColumnFamilyHandle.weakref` lazy cache | Self-contained, GIL-held check-then-act. Cheaper than a mutex and the idiomatic Cython fix for lazy-init / TOCTOU on a single object. |

### The per-instance lifecycle lock (`DB` and `TransactionDB`)

A `cython.pymutex` field guards exactly these regions:

1. **`close()` / `__dealloc__` self-race (B2).** Re-check and null `wrapped_db`
   *under the lock* so a second `close()` (or a GC-driven `__dealloc__` running
   on another thread concurrently with an explicit `close()`) is a no-op rather
   than a double `Close()` / double-free. `del self.cf_handles[:]` /
   `del self.cf_options[:]` likewise run once.
2. **Column-family management (B1).** `create_column_family`,
   `drop_column_family`, `get_column_family`: the entire read-modify-write — the
   duplicate-name scan, the C++ call, and the mutation of *both* parallel lists
   (`cf_handles`, `cf_options`) — is atomic. Eliminates list desync and stale
   `index()` deletions.
3. **Child teardown (B6).** `Snapshot._force_close` and iterator `_force_close`
   coordinate with `close()` through the same lock, so a snapshot/iterator
   `__dealloc__` triggered by GC **on any thread** cannot double-release or
   use-after-close while `DB.close()` is tearing the DB down.

The **data path is deliberately not locked.** `get`/`put`/`delete`/`write`/
`multi_get`/`key_may_exist`/iterator-creation read `wrapped_db` without the lock.
This is safe *under the documented contract* that the user does not close a DB
while other threads still operate on it — closing concurrently with live ops
remains a usage error (it would be one even with the GIL).

### Reentrancy discipline

`cython.pymutex` is **non-reentrant**, and `close()` (lock held) calls into child
`_force_close`. To avoid self-deadlock:

- private `_*_locked()` helpers assume the lock is already held and do the work;
- public entry points (`DB.close()`, `DB.__dealloc__`, `TransactionDB.close()`,
  `Snapshot.__dealloc__`, iterator `__dealloc__`) acquire the lock, then delegate
  to the `_locked` helper.

So `DB.close()` takes the lock once and calls the lock-free child-release helpers
directly; a standalone `Snapshot.__dealloc__` takes the DB lock itself.

### Object-local TOCTOUs (`critical_section`)

- **`Options.in_use` (B3).** `cython.critical_section(self)` around the
  check-then-set at every attach site (`DB.__init__`, per-CF options,
  `create_column_family`, `TransactionDB`). The lock is on the **Options
  object** (not the DB) because that is the resource shared across DBs — this is
  what makes "one mutable `Options` backs two DBs" impossible.
- **`_ColumnFamilyHandle.weakref` lazy cache (B7).** `cython.critical_section(self)`
  around the get-or-create of the cached `weakref`, so two threads cannot mint
  divergent wrapper objects that would compare `==` but hash differently
  (corrupting set/dict-key usage).

### Single-thread-owned objects

`Iterator` and `WriteBatch` are **documented as not shareable across threads**
(they are inherently single-cursor / single-builder; this matches CPython's own
stance on iterators). No per-step locking is added on their hot path. Their
*lifecycle vs DB teardown* is still synchronized via the per-instance lock
(the GC-driven `_force_close` race above).

### C++ trampolines (B8)

The wrappers (`comparator_wrapper.hpp`, `merge_operator_wrapper.hpp`,
`slice_transform_wrapper.hpp`) currently store a **borrowed** `PyObject*` and
rely on ordering (`self.ob = ob` pinning) to keep it alive.

- Add `Py_INCREF` of the stored context in each wrapper constructor and
  `Py_DECREF` in a new destructor → lifetime becomes **refcount-based**, robust
  against out-of-order interpreter finalization, instead of ordering-based. The
  DECREF is guarded by `PyGILState_Ensure`/`PyGILState_Release` so it holds a
  valid thread-state even if it runs during teardown. (Caveat to review: DECREF
  during interpreter finalization — keep it conservative.)
- **Document** that user comparators / merge-operators / slice-transforms must
  be internally thread-safe. We do *not* serialize the callbacks: RocksDB runs
  them concurrently across parallel subcompactions and serializing them would
  defeat the parallelism FT exists to provide.

### Declaring FT support — added last

Add `# cython: freethreading_compatible = True` to the `rocksdb/_rocksdb.pyx`
header (next to `language_level=3`). Cython then emits
`Py_mod_gil = Py_MOD_GIL_NOT_USED`, so import no longer re-enables the GIL. This
is a *promise of correctness*, so it lands only after all the synchronization
work above. (Equivalent alternative: `compiler_directives` in `setup.py`; we use
the in-module header for locality and discoverability.)

### Other handle-owning classes

Audit `BackupEngine` and any other cdef class that owns a raw C++ pointer with a
`close()`/`__dealloc__` pair for the same self-race pattern, and apply the
lifecycle-lock treatment if present.

## Testing

New `rocksdb/tests/test_concurrency.py`:

- A module-level guard that skips (with a clear reason) when not running on a
  free-threaded build, and asserts `not sys._is_gil_enabled()` when it is.
- `threading.Barrier`-synchronized, high-iteration stress for each race:
  - **B1** — many threads concurrently `create_column_family` /
    `drop_column_family` on one DB; assert list invariants hold.
  - **B2** — `close()` racing live `get`/`put`, plus a GC-triggered
    `__dealloc__`; assert no crash / double-close (within the documented
    contract — i.e. close-then-stop, not close-during-op).
  - **B3** — one `Options` handed to two `DB()` constructors concurrently;
    exactly one must win / the other must raise.
  - **B6** — snapshot GC racing `close()`.
  - **B7** — concurrent `handle.weakref` access asserting `hash`/`eq`
    consistency.
  - **trampolines** — a custom Python comparator and merge-operator driven under
    concurrent writes/compactions.

These are regression guards: expected to crash/corrupt *before* the fixes, pass
after.

## CI

- **`build.yml`:** add `3.14t` to the `py_ver` matrix (`actions/setup-python@v6`
  supports the `t` variants natively; `allow-prereleases: true` already covers
  `3.14t`). For that leg, export `PYTHON_GIL=0` and assert the GIL is actually
  off. The full suite (incl. `test_concurrency.py`) runs across both RocksDB
  versions, unchanged otherwise.
- **ThreadSanitizer leg** (new `freethreading.yml`, or a dedicated job):
  1. Build RocksDB with `-fsanitize=thread` (cached under a separate key).
  2. Build CPython **3.14t from source** with
     `--with-thread-sanitizer --disable-gil` (cached).
  3. Build the extension with `-fsanitize=thread`.
  4. Run `test_concurrency.py` under TSan with a curated suppressions file
     (modeled on CPython's `Tools/tsan/suppressions_free_threading.txt`) and
     `TSAN_OPTIONS=halt_on_error=1`.
- **Docs:** a "Thread safety" section covering what is safe to share
  concurrently, the close-vs-operate rule, the single-thread-owned objects, and
  the user-callback thread-safety requirement.

## Risks

- **The TSan leg is the heaviest and most fragile piece.** The suppressions file
  will need iteration (both RocksDB and CPython have known/benign races). Plan to
  tune it until green.
- **C++ destructor `Py_DECREF` during finalization** — guarded with
  `PyGILState_Ensure`, but a genuine edge case; review carefully.
- The per-instance lock serializes column-family management per DB; cf ops are
  rare, so this is acceptable. The data path is untouched, preserving the
  read/write parallelism that is the point of free-threading.

## Commit plan

Clean, logically separate commits on `main`:

1. spec doc (this file)
2. object-local TOCTOU fixes (`Options.in_use`, `_ColumnFamilyHandle.weakref`)
3. per-instance lifecycle lock for `DB`/`TransactionDB` (+ child teardown,
   reentrancy helpers, `BackupEngine` audit)
4. C++ trampoline `Py_INCREF`/`Py_DECREF`
5. `freethreading_compatible` directive
6. `test_concurrency.py`
7. CI: `build.yml` `3.14t` leg
8. CI: TSan `freethreading.yml`
9. docs: thread-safety section
