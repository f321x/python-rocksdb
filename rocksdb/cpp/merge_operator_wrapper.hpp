#include "rocksdb/merge_operator.h"

using std::string;
using std::deque;
using rocksdb::Slice;
using rocksdb::Logger;
using rocksdb::MergeOperator;
using rocksdb::AssociativeMergeOperator;

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
    class AssociativeMergeOperatorWrapper: public AssociativeMergeOperator {
        public:
            typedef bool (*merge_func)(
                    void*, 
                    const Slice& key,
                    const Slice* existing_value,
                    const Slice& value,
                    std::string* new_value,
                    Logger* logger);


            AssociativeMergeOperatorWrapper(
                string name,
                void* merge_context,
                merge_func merge_callback):
                    name(name),
                    merge_context(merge_context),
                    merge_callback(merge_callback)
            {
                Py_XINCREF((PyObject*)this->merge_context);
            }

            virtual ~AssociativeMergeOperatorWrapper() {
                pyrocks_decref_context(this->merge_context);
            }

            // Owns a Python reference; forbid copying (held only by shared_ptr).
            AssociativeMergeOperatorWrapper(const AssociativeMergeOperatorWrapper&) = delete;
            AssociativeMergeOperatorWrapper& operator=(const AssociativeMergeOperatorWrapper&) = delete;

            virtual bool Merge(
                const Slice& key,
                const Slice* existing_value,
                const Slice& value,
                std::string* new_value,
                Logger* logger) const 
            {
                return this->merge_callback(
                    this->merge_context,
                    key,
                    existing_value,
                    value,
                    new_value,
                    logger);
            }

            virtual const char* Name() const {
                return this->name.c_str();
            }

        private:
            string name;
            void* merge_context;
            merge_func merge_callback;
    };

    class MergeOperatorWrapper: public MergeOperator {
        public:
            typedef bool (*full_merge_func)(
                void* ctx,
                const Slice& key,
                const Slice* existing_value,
                const deque<string>& operand_list,
                string* new_value,
                Logger* logger);

            typedef bool (*partial_merge_func)(
                void* ctx,
                const Slice& key,
                const Slice& left_op,
                const Slice& right_op,
                string* new_value,
                Logger* logger);

            MergeOperatorWrapper(
                string name,
                void* full_merge_context,
                void* partial_merge_context,
                full_merge_func full_merge_callback,
                partial_merge_func partial_merge_callback):
                    name(name),
                    full_merge_context(full_merge_context),
                    partial_merge_context(partial_merge_context),
                    full_merge_callback(full_merge_callback),
                    partial_merge_callback(partial_merge_callback)
            {
                // The two contexts are usually the same Python object; incref
                // each and decref each so the count stays balanced regardless.
                Py_XINCREF((PyObject*)this->full_merge_context);
                Py_XINCREF((PyObject*)this->partial_merge_context);
            }

            virtual ~MergeOperatorWrapper() {
                pyrocks_decref_context(this->full_merge_context);
                pyrocks_decref_context(this->partial_merge_context);
            }

            // Owns Python references; forbid copying (held only by shared_ptr).
            MergeOperatorWrapper(const MergeOperatorWrapper&) = delete;
            MergeOperatorWrapper& operator=(const MergeOperatorWrapper&) = delete;

            virtual bool FullMerge(
                const Slice& key,
                const Slice* existing_value,
                const deque<string>& operand_list,
                string* new_value,
                Logger* logger) const 
            {
                return this->full_merge_callback(
                    this->full_merge_context,
                    key,
                    existing_value,
                    operand_list,
                    new_value,
                    logger);
            }

            virtual bool PartialMerge (
                const Slice& key,
                const Slice& left_operand,
                const Slice& right_operand,
                string* new_value,
                Logger* logger) const
            {
                return this->partial_merge_callback(
                    this->partial_merge_context,
                    key,
                    left_operand,
                    right_operand,
                    new_value,
                    logger);
            }
            
            virtual const char* Name() const {
                return this->name.c_str();
            }

        private:
            string name;
            void* full_merge_context;
            void* partial_merge_context;
            full_merge_func full_merge_callback;
            partial_merge_func partial_merge_callback;

        };
}
