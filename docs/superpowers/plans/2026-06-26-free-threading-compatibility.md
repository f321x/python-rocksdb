# Free-threading compatibility — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Make `rocksdb-ng` import and run correctly on a free-threaded CPython build, declare it (`Py_mod_gil = Py_MOD_GIL_NOT_USED`), and cover it in CI (3.14t + ThreadSanitizer).

**Architecture:** Per-instance `cython.pymutex` "lifecycle lock" on `DB`/`TransactionDB` guarding `close()`/`__dealloc__` self-races, column-family management, and child (snapshot/iterator) teardown. Object-local `cython.critical_section` for the `Options.in_use` claim and the CF-handle weakref cache. Refcount-based trampoline lifetimes (`Py_INCREF`/`Py_DECREF`). Data path stays lock-free. Directive added last.

**Tech stack:** Cython (`>=3.2.5,<4`, verified 3.2.6 has `cython.pymutex`/`cython.critical_section`), RocksDB 9.x–10.x, C++20.

## Global constraints (verbatim from spec)

- Cython floor `>=3.2.5,<4` — no bump. `cython.pymutex` → `PyMutex` on 3.13+, `PyThread_type_lock` fallback on 3.11/3.12 (GIL-ordering-safe). `cython.critical_section` → `PyCriticalSection` on FT, no-op otherwise. Both verified to compile as cdef-class attribute / context-manager.
- Compiles as C++20 (`-std=c++20`), single extension module `rocksdb._rocksdb`.
- The `freethreading_compatible` directive is a *promise* — it lands **last**, only after all synchronization is in place.
- Data path (`get`/`put`/`delete`/`write`/`multi_get`/`key_may_exist`/iterator-creation) stays lock-free; documented contract: don't close a DB while other threads still operate on it.
- `cython.pymutex` is non-reentrant → `_locked` helper discipline.
- Stress tests **run on every build** (catch deadlocks/logic errors under the GIL too), not skipped off-FT.

## Local build/verify loop

```bash
. <venv>/bin/activate              # venv with Cython>=3.2.5, pytest, pkgconfig
python setup.py build_ext --inplace   # rebuild after .pyx/.hpp edits
python -m pytest -q rocksdb/tests/test_db.py rocksdb/tests/test_lifecycle.py \
    rocksdb/tests/test_transaction_db.py   # regression (in-tree .so)
python -m pytest -q rocksdb/tests/test_concurrency.py    # new threaded stress (runs under GIL too)
```

(Local interpreter is GIL-3.13 + RocksDB 9.10; FT/TSan validation happens in CI.)

---

## Task 1: spec doc  ✅ (committed: `Add free-threading compatibility design spec`)

---

## Task 2: object-local TOCTOU fixes (`Options.in_use`, CF-handle weakref)

**Files:** Modify `rocksdb/_rocksdb.pyx`.

**Produces:** `cdef bint ColumnFamilyOptions.try_acquire(self) noexcept`, `cdef bint TransactionDBOptions.try_acquire(self) noexcept`.

- [ ] **Weakref cache (B7)** — `_ColumnFamilyHandle.weakref` (≈:699):
```cython
    @property
    def weakref(self):
        with cython.critical_section(self):
            if self.weak_handle is None:
                self.weak_handle = ColumnFamilyHandle.from_wrapper(self)
            return self.weak_handle
```

- [ ] **`try_acquire` on `ColumnFamilyOptions`** (add method to class at :774, inherited by `Options`):
```cython
    cdef bint try_acquire(self) noexcept:
        # Atomic claim: a mutable (Column)Options must not back two DBs/CFs.
        with cython.critical_section(self):
            if self.in_use:
                return False
            self.in_use = True
            return True
```
- [ ] **`try_acquire` on `TransactionDBOptions`** (class at :1622) — identical body.

- [ ] **`DB.__cinit__` (:1834+)** — replace the `opts.in_use` check + the later `self.opts.in_use = True` (in `inject_loggers` :1948) with an early atomic claim, and release every claim on failed open. Claim main opts before building descriptors; claim each CF opts via `try_acquire`; wrap descriptor-build + open + `post_init_steps` in `try/except` that releases `opts` and all claimed CF opts on failure. Remove `self.opts.in_use = True` from `inject_loggers`. (`self.opts = opts` stays in `inject_loggers`; close() still releases.)
  - Replace `if opts.in_use: raise InvalidArgument(...)` → `if not opts.try_acquire(): raise InvalidArgument("Options object is already used by another DB")`.
  - Replace per-CF `if (<ColumnFamilyOptions>cf_options).in_use: raise ...` + `(<...>).in_use = True` → `if not (<ColumnFamilyOptions>cf_options).try_acquire(): raise Exception(...)` and track in a local `claimed` list.
  - `except:` → `opts.in_use = False; for co in claimed: (<ColumnFamilyOptions>co).in_use = False; raise`.
- [ ] **`create_column_family` (:2495)** — replace `if copts.in_use: raise; copts.in_use = True` with `if not copts.try_acquire(): raise Exception(...)`, and release `copts.in_use = False` if `CreateColumnFamily` fails. (Lock added in Task 3.)
- [ ] **`TransactionDB.__cinit__` (:2563)** — replace `if tdb_opts.in_use: raise; ... self.tdb_opts.in_use = True` with `if not tdb_opts.try_acquire(): raise InvalidArgument(...)`; release on failed open.

- [ ] **Build + regression + verify:** `python setup.py build_ext --inplace` then run `test_db.py`, `test_lifecycle.py`, `test_options.py`, `test_transaction_db.py`, `test_transactiondb_options.py` — all pass; the "Options already used by another DB" tests still raise.
- [ ] **Commit:** `Make Options/CF-handle TOCTOUs atomic for free-threading`

---

## Task 3: per-instance lifecycle lock (`DB`/`TransactionDB`)

**Files:** Modify `rocksdb/_rocksdb.pyx`.

**Consumes:** Task 2's `try_acquire`. **Produces:** `cdef cython.pymutex DB._lock`; `cdef _force_close_locked(self)` on `Snapshot` and `BaseIterator`.

- [ ] **Add lock field** to `DB` (:1822 area): `cdef cython.pymutex _lock`. (Inherited by `TransactionDB`.)
- [ ] **Split child teardown** — `Snapshot` (:2625) and `BaseIterator` (:2652):
  - rename the worker body to `cdef _force_close_locked(self)` (assumes the DB lock is held or no DB present);
  - public `def _force_close(self)` acquires the DB lock then delegates:
```cython
    def _force_close(self):
        cdef DB db = self.db
        if db is None:
            self._force_close_locked()
            return
        with db._lock:
            self._force_close_locked()
```
- [ ] **`_release_children` (:1956)** — runs with the lock already held (called only from `close()`); type the loop vars so the `cdef` worker is callable:
```cython
    cdef _release_children(self):
        cdef BaseIterator it
        cdef Snapshot snap
        if self._iterators is not None:
            for it in list(self._iterators):
                it._force_close_locked()
            self._iterators.clear()
        if self._snapshots is not None:
            for snap in list(self._snapshots):
                snap._force_close_locked()
            self._snapshots.clear()
```
- [ ] **`DB.close()` (:1973)** — wrap body in `with self._lock:`; early-`return` if `wrapped_db == NULL`; null `wrapped_db` (into a local) under the lock before the `nogil` `Close()`:
```cython
    def close(self, safe=True):
        cdef ColumnFamilyOptions copts
        cdef cpp_bool c_safe = safe
        cdef Status st
        cdef db.DB* wrapped
        with self._lock:
            if self.wrapped_db == NULL:
                return
            with nogil:
                db.CancelAllBackgroundWork(self.wrapped_db, c_safe)
            self._release_children()
            del self.cf_handles[:]
            self.column_family_handles.clear()
            for copts in self.cf_options:
                if copts:
                    copts.in_use = False
            del self.cf_options[:]
            wrapped = self.wrapped_db
            self.wrapped_db = NULL
            with nogil:
                st = wrapped.Close()
            if self.opts is not None:
                self.opts.in_use = False
            check_status(st)
```
- [ ] **`TransactionDB.close()` (:2589)** — same transformation, casting `wrapped` to `transaction_db.TransactionDB*` for `Close()`, and adding the `self.tdb_opts.in_use = False` release inside the lock.
- [ ] **`create_column_family` / `drop_column_family` / `get_column_family` / `column_families`** — wrap each body in `with self._lock:` so the cf-list read-modify-write (and the C++ call) is atomic. Add a `if self.wrapped_db == NULL: raise InvalidArgument("DB is closed")` guard inside the lock for create/drop.
- [ ] **`BackupEngine` audit** — confirm whether it has a `close()`/`__dealloc__` self-race; if so, give it the same treatment; otherwise note "no shared lifecycle pointer race" in the commit message.
- [ ] **Build + regression:** rebuild; run full existing suite — all pass (proves no reentrant-`pymutex` deadlock in `close()`→`_release_children`→`_force_close_locked`, and no deadlock from nested `with self._lock` + `with nogil`).
- [ ] **Commit:** `Add per-DB lifecycle lock (pymutex) for free-threading`

---

## Task 4: trampoline `Py_INCREF`/`Py_DECREF` (C++)

**Files:** Modify `rocksdb/cpp/comparator_wrapper.hpp`, `merge_operator_wrapper.hpp`, `slice_transform_wrapper.hpp`.

- [ ] Add to each wrapper: `#include <Python.h>` (guarded), a finalizing macro, `Py_INCREF` the stored context pointer(s) in the ctor, and a destructor that DECREFs under `PyGILState_Ensure` (skipping during interpreter finalization):
```cpp
#include <Python.h>
#if PY_VERSION_HEX >= 0x030D0000
  #define PYROCKS_IS_FINALIZING() Py_IsFinalizing()
#else
  #define PYROCKS_IS_FINALIZING() _Py_IsFinalizing()
#endif
// in ctor, after storing the context:  Py_XINCREF((PyObject*)compare_context);
// destructor:
virtual ~ComparatorWrapper() {
    if (compare_context && !PYROCKS_IS_FINALIZING()) {
        PyGILState_STATE g = PyGILState_Ensure();
        Py_DECREF((PyObject*)compare_context);
        PyGILState_Release(g);
    }
}
```
  - `MergeOperatorWrapper` holds `full_merge_context` and `partial_merge_context` (same `ob` passed twice) — INCREF both in ctor, DECREF both in dtor (balanced). `AssociativeMergeOperatorWrapper`: single `merge_context`. `SliceTransformWrapper`: single `ctx`.
- [ ] **Build + regression:** rebuild; run `test_db.py` (custom comparator/merge tests) — pass. Run a quick create→use-custom-comparator→close→gc loop to confirm no double-free / no leak crash.
- [ ] **Commit:** `Make trampoline callback contexts refcount-owned (free-threading)`

---

## Task 5: `freethreading_compatible` directive

**Files:** Modify `rocksdb/_rocksdb.pyx:1`.

- [ ] Add header directive next to `language_level`:
```cython
# cython: language_level=3
# cython: freethreading_compatible=True
```
- [ ] **Build + verify the slot:** rebuild; confirm the generated C defines the gil-not-used slot (`grep -c Py_MOD_GIL_NOT_USED rocksdb/_rocksdb.cpp` ≥ 1). Run full suite on GIL build — still passes (directive is inert under the GIL).
- [ ] **Commit:** `Declare the extension free-threading compatible`

---

## Task 6: concurrency stress tests

**Files:** Create `rocksdb/tests/test_concurrency.py`.

- [ ] Write `threading.Barrier`-synchronized, high-iteration tests, each in a fresh `tempfile.mkdtemp` DB, **running on every build**:
  - `test_concurrent_create_drop_column_family` (B1) — N threads create/drop distinct CFs on one DB; assert `len(cf_handles)==len(cf_options)` invariant survives and no crash.
  - `test_close_races_operations` (B2) — within the documented contract: many threads `put`/`get`, then a single `close()`; plus a separate test dropping the only ref so GC `__dealloc__` runs while another DB op proceeds — assert no double-close crash.
  - `test_shared_options_rejected_concurrently` (B3) — two threads construct `DB` from one `Options`; exactly one succeeds, the other raises `InvalidArgument`.
  - `test_snapshot_gc_vs_close` (B6) — create+drop snapshots on threads while closing.
  - `test_weakref_hash_eq_consistency` (B7) — many threads read `handle.weakref`; assert all returned wrappers are `==` and have equal `hash`.
  - `test_concurrent_custom_comparator` — open a DB with a Python comparator/merge-operator, hammer with concurrent writes triggering compaction.
  - `test_gil_status` — informational: prints `sys._is_gil_enabled()`; on a non-FT build it's skip/xfail-tolerant (does not fail the GIL build).
- [ ] **Run:** `python -m pytest -q rocksdb/tests/test_concurrency.py` — green under GIL (no deadlocks, no logic errors).
- [ ] **Commit:** `Add free-threading concurrency stress tests`

---

## Task 7: CI — 3.14t leg in `build.yml`

**Files:** Modify `.github/workflows/build.yml`.

- [ ] Add `'3.14t'` to the `py_ver` matrix (`setup-python@v6` + `allow-prereleases: true` already present).
- [ ] For the 3.14t leg, set `PYTHON_GIL=0` (env) and add a step asserting `not sys._is_gil_enabled()` (so the leg actually exercises FT and fails loudly if the GIL silently re-enables).
- [ ] **Verify:** `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml')); print('yaml ok')"`.
- [ ] **Commit:** `CI: test on free-threaded Python 3.14t`

---

## Task 8: CI — ThreadSanitizer leg (`freethreading.yml`)

**Files:** Create `.github/workflows/freethreading.yml`; create `contrib/tsan/suppressions.txt`.

- [ ] New workflow:
  1. Build RocksDB 10.x with `-fsanitize=thread` (cached under a TSan-specific key; reuse `build.yml`'s build steps with `CXXFLAGS="-fsanitize=thread -g"`).
  2. Build CPython 3.14t from source: `./configure --with-thread-sanitizer --disable-gil --prefix=...` (cached).
  3. `pip install` the extension built against the TSan RocksDB with `CFLAGS/LDFLAGS += -fsanitize=thread`.
  4. Run `pytest rocksdb/tests/test_concurrency.py` under `PYTHON_GIL=0 TSAN_OPTIONS="halt_on_error=1 suppressions=contrib/tsan/suppressions.txt"`.
- [ ] Seed `contrib/tsan/suppressions.txt` from CPython's `Tools/tsan/suppressions_free_threading.txt` plus RocksDB-internal entries; document it's expected to need tuning.
- [ ] **Verify:** YAML parses.
- [ ] **Commit:** `CI: ThreadSanitizer leg for free-threading`

---

## Task 9: docs — thread-safety section

**Files:** Modify `docs/` (add a thread-safety page or section; wire into `index.rst`).

- [ ] Document: what's safe to share concurrently (one `DB` for `get`/`put`/`delete`/`write`/iterator-creation/cf-management/`close`); the close-vs-operate contract; `Iterator`/`WriteBatch` single-thread-owned; custom comparators/merge-operators/slice-transforms must be internally thread-safe; the `rocksdb-ng` ↔ upstream `rocksdb` co-install note already exists.
- [ ] **Verify:** `sphinx-build -W -b html docs docs/_build/html` succeeds (warnings = errors).
- [ ] **Commit:** `docs: document the free-threading thread-safety model`

---

## Self-review

- **Spec coverage:** A→Tasks 1–9; B1→T3, B2→T3, B3→T2, B4→T3 (lifecycle) + docs (sharing), B5→docs, B6→T3, B7→T2, B8→T4+docs; C(directive)→T5; D(CI)→T6/7/8; E decisions all reflected. ✓
- **Placeholders:** none — every code step has real code or an exact command.
- **Type consistency:** `try_acquire` (T2) consumed in T2 sites; `_force_close_locked` defined + called consistently (T3); `_lock` field name consistent.
