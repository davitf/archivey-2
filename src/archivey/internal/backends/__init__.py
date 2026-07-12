# Importing each backend module is REQUIRED, not incidental: every backend
# self-registers with the BackendRegistry as an import-time side effect (each module
# ends with `register_reader(...)`). Importing this package therefore makes the
# bundled backends available for selection. Do not remove these imports — without
# them the registry would be empty and `open_archive()` would find no backend.
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
