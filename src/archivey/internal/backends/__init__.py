"""Format backends — one self-registering reader module per archive format.

Importing this package is required: each submodule ends with ``register_reader(...)``,
so a bare ``import archivey.internal.backends`` fills the registry. Do not remove the
side-effect imports below.

Module map:

- :mod:`.zip_reader` — ZIP (stdlib central directory; codecs/crypto for member data)
- :mod:`.tar_reader` — TAR / compressed TAR (stdlib ``tarfile``)
- :mod:`.iso_reader` — ISO 9660 (``pycdlib``, ``[iso]``)
- :mod:`.directory_reader` — filesystem directory as a pseudo-archive
- :mod:`.single_file_reader` — bare ``.gz`` / ``.xz`` / … as a one-member archive
- :mod:`.sevenzip_methods` / ``sevenzip_parser`` / ``sevenzip_pipeline`` /
  ``sevenzip_reader`` — native 7z (method registry → header parse → folder decode → ABC)
- :mod:`.rar_parser` / ``rar_unrar`` / ``rar_reader`` — native RAR metadata + ``unrar`` data

Typical split inside a format: ``*ReadBackend`` (registry / ``open_read``) +
``*Reader`` (``BaseArchiveReader``). Native 7z/RAR also split parse vs decode.
"""

from archivey.internal.backends import (
    directory_reader as _directory_reader,  # noqa: F401
)
from archivey.internal.backends import iso_reader as _iso_reader  # noqa: F401
from archivey.internal.backends import rar_reader as _rar_reader  # noqa: F401
from archivey.internal.backends import (
    sevenzip_reader as _sevenzip_reader,  # noqa: F401
)
from archivey.internal.backends import (
    single_file_reader as _single_file_reader,  # noqa: F401
)
from archivey.internal.backends import tar_reader as _tar_reader  # noqa: F401
from archivey.internal.backends import zip_reader as _zip_reader  # noqa: F401
