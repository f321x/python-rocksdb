from libcpp cimport bool as cpp_bool
from libcpp.string cimport string
from libc.string cimport const_char
from .slice_ cimport Slice
from .std_memory cimport shared_ptr
from .logger cimport Logger

cdef extern from "rocksdb/filter_policy.h" namespace "rocksdb":
    cdef cppclass FilterPolicy:
        const_char* Name() except+ nogil

    cdef extern const FilterPolicy* NewBloomFilterPolicy(double) except+ nogil
