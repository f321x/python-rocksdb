Installing
==========
.. highlight:: bash


With distro package and pypi
****************************

This requires librocksdb-dev>=5.0

.. code-block:: bash

    apt-get install python-virtualenv python-dev librocksdb-dev
    virtualenv venv
    source venv/bin/activate
    pip install rocksdb-ng

.. note::

    The PyPI distribution is named ``rocksdb-ng`` (the plain ``rocksdb`` name is
    taken by the upstream project this is forked from). The import name is
    unchanged: after installing you still ``import rocksdb``.

.. warning::

    ``rocksdb-ng`` installs the same top-level ``rocksdb`` import package as the
    original ``rocksdb`` distribution, so the two **cannot coexist** in one
    environment — they would overwrite each other's files and pip will *not*
    flag the conflict. If you are migrating from the original ``rocksdb``,
    uninstall it first::

        pip uninstall rocksdb
        pip install rocksdb-ng

From source
***********

Building rocksdb
----------------

Briefly describes how to build rocksdb under an ordinary debian/ubuntu.
For more details consider https://github.com/facebook/rocksdb/blob/master/INSTALL.md

.. code-block:: bash

    apt-get install build-essential libsnappy-dev zlib1g-dev libbz2-dev libgflags-dev
    git clone https://github.com/facebook/rocksdb.git
    cd rocksdb
    mkdir build && cd build
    cmake ..
    make

Systemwide rocksdb
^^^^^^^^^^^^^^^^^^
The following command installs the shared library in ``/usr/lib/`` and the
header files in ``/usr/include/rocksdb/``::

    make install-shared INSTALL_PATH=/usr

To uninstall use::

    make uninstall INSTALL_PATH=/usr

Local rocksdb
^^^^^^^^^^^^^
If you don't like the system wide installation, or you don't have the
permissions, it is possible to set the following environment variables.
These varialbes are picked up by the compiler, linker and loader

.. code-block:: bash

    export CPLUS_INCLUDE_PATH=${CPLUS_INCLUDE_PATH}:`pwd`/../include
    export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:`pwd`
    export LIBRARY_PATH=${LIBRARY_PATH}:`pwd`

Building python-rocksdb
-----------------------

.. code-block:: bash

    apt-get install python-virtualenv python-dev
    virtualenv venv
    source venv/bin/activate
    pip install git+https://github.com/f321x/python-rocksdb.git#egg=rocksdb-ng
