from libcpp.memory cimport shared_ptr
from .db cimport DB

cdef extern from "rocksdb/utilities/stackable_db.h" namespace "rocksdb":
    cdef cppclass StackableDB(DB):
        StackableDB(DB*) except+ nogil
        StackableDB(shared_ptr[DB] db) except+ nogil
        DB* GetBaseDB() except+ nogil
