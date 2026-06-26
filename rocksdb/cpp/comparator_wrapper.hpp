#include "rocksdb/comparator.h"
#include "rocksdb/env.h"
#include <stdexcept>

using std::string;
using rocksdb::Comparator;
using rocksdb::Slice;
using rocksdb::Logger;

// Shared helper: release a borrowed Python callback context held by a
// trampoline wrapper. The wrappers Py_INCREF their context in the constructor
// so the Python object cannot be torn down while RocksDB might still invoke the
// callback (out-of-order interpreter finalization, or a background subcompaction
// holding the only reference). The matching decref must hold a thread-state, so
// we ensure the GIL; we skip it during interpreter finalization, where
// re-attaching a thread-state is unsafe and a leaked ref is harmless.
#ifndef PYROCKS_DECREF_CONTEXT_DEFINED
#define PYROCKS_DECREF_CONTEXT_DEFINED
#include <Python.h>
static inline void pyrocks_decref_context(void* ctx) {
    if (ctx == nullptr) return;
#if PY_VERSION_HEX >= 0x030D0000
    if (Py_IsFinalizing()) return;
#else
    if (_Py_IsFinalizing()) return;
#endif
    PyGILState_STATE gstate = PyGILState_Ensure();
    Py_DECREF((PyObject*)ctx);
    PyGILState_Release(gstate);
}
#endif

namespace py_rocks {
    class ComparatorWrapper: public Comparator {
        public:
            typedef int (*compare_func)(
                void*,
                Logger*,
                string&,
                const Slice&,
                const Slice&);

            ComparatorWrapper(
                string name,
                void* compare_context,
                compare_func compare_callback):
                    name(name),
                    compare_context(compare_context),
                    compare_callback(compare_callback)
            {
                // Constructed from Cython with the GIL held; safe to incref.
                Py_XINCREF((PyObject*)this->compare_context);
            }

            virtual ~ComparatorWrapper() {
                pyrocks_decref_context(this->compare_context);
            }

            virtual int Compare(const Slice& a, const Slice& b) const {
                string error_msg;
                int val;

                val = this->compare_callback(
                    this->compare_context,
                    this->info_log.get(),
                    error_msg,
                    a,
                    b);

                if (error_msg.size()) {
                    throw std::runtime_error(error_msg.c_str());
                }
                return val;
            }

            virtual const char* Name() const {
                return this->name.c_str();
            }

            virtual void FindShortestSeparator(string*, const Slice&) const {}
            virtual void FindShortSuccessor(string*) const {}

            void set_info_log(std::shared_ptr<Logger> info_log) {
                this->info_log = info_log;
            }

        private:
            string name;
            void* compare_context;
            compare_func compare_callback;
            std::shared_ptr<Logger> info_log;
    };
}
