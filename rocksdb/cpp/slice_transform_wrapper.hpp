#include <string>
#include "rocksdb/slice_transform.h"
#include "rocksdb/env.h"
#include <stdexcept>

using std::string;
using rocksdb::SliceTransform;
using rocksdb::Slice;
using rocksdb::Logger;

// See comparator_wrapper.hpp for the rationale; the guard makes this a no-op
// when that header is also compiled into the same translation unit.
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
    class SliceTransformWrapper: public SliceTransform {
        public:
            typedef Slice (*transform_func)(
                void*,
                Logger*,
                string&,
                const Slice&);

            typedef bool (*in_domain_func)(
                void*,
                Logger*,
                string&,
                const Slice&);

            typedef bool (*in_range_func)(
                void*,
                Logger*,
                string&,
                const Slice&);

            SliceTransformWrapper(
                string name,
                void* ctx,
                transform_func transform_callback,
                in_domain_func in_domain_callback,
                in_range_func in_range_callback):
                    name(name),
                    ctx(ctx),
                    transform_callback(transform_callback),
                    in_domain_callback(in_domain_callback),
                    in_range_callback(in_range_callback)
            {
                Py_XINCREF((PyObject*)this->ctx);
            }

            virtual ~SliceTransformWrapper() {
                pyrocks_decref_context(this->ctx);
            }

            // Owns a Python reference; forbid copying (held only by shared_ptr).
            SliceTransformWrapper(const SliceTransformWrapper&) = delete;
            SliceTransformWrapper& operator=(const SliceTransformWrapper&) = delete;

            virtual const char* Name() const {
                return this->name.c_str();
            }

            virtual Slice Transform(const Slice& src) const {
                string error_msg;
                Slice val;

                val = this->transform_callback(
                    this->ctx,
                    this->info_log.get(),
                    error_msg,
                    src);

                if (error_msg.size()) {
                    throw std::runtime_error(error_msg.c_str());
                }
                return val;
            }

            virtual bool InDomain(const Slice& src) const {
                string error_msg;
                bool val;

                val = this->in_domain_callback(
                    this->ctx,
                    this->info_log.get(),
                    error_msg,
                    src);

                if (error_msg.size()) {
                    throw std::runtime_error(error_msg.c_str());
                }
                return val;
            }

            virtual bool InRange(const Slice& dst) const {
                string error_msg;
                bool val;

                val = this->in_range_callback(
                    this->ctx,
                    this->info_log.get(),
                    error_msg,
                    dst);

                if (error_msg.size()) {
                    throw std::runtime_error(error_msg.c_str());
                }
                return val;
            }

            void set_info_log(std::shared_ptr<Logger> info_log) {
                this->info_log = info_log;
            }

        private:
            string name;
            void* ctx;
            transform_func transform_callback;
            in_domain_func in_domain_callback;
            in_range_func in_range_callback;
            std::shared_ptr<Logger> info_log;
    };
}
