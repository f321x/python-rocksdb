from libcpp cimport bool as cpp_bool
from libcpp.string cimport string

from .std_memory cimport shared_ptr
from .env cimport Env
from .status cimport Status

cdef extern from "rocksdb/convenience.h" namespace "rocksdb":
    cdef cppclass ConfigOptions:
        ConfigOptions() except +
        cpp_bool ignore_unsupported_options

cdef extern from "rocksdb/env_encryption.h" namespace "rocksdb":
    cdef cppclass EncryptionProvider:
        # GetId/IsInstanceOf are inherited from Customizable/Configurable;
        # declared directly here since only this subset is used.
        string GetId() except+ nogil
        cpp_bool IsInstanceOf(const string&) except+ nogil
        Status AddCipher(const string&, const char*, size_t, cpp_bool) except+ nogil
        size_t GetPrefixLength() except+ nogil

    cdef Status EncryptionProvider_CreateFromString "rocksdb::EncryptionProvider::CreateFromString"(
        const ConfigOptions&,
        const string&,
        shared_ptr[EncryptionProvider]*) except+ nogil

    cdef Env* NewEncryptedEnv(Env*, const shared_ptr[EncryptionProvider]&) except+ nogil
