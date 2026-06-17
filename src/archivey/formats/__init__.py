# Importing each backend module is REQUIRED, not incidental: every backend
# self-registers with the BackendRegistry as an import-time side effect (each module
# ends with `register_reader(...)`). Importing this package therefore makes the
# bundled backends available for selection. Do not remove these imports — without
# them the registry would be empty and `open_archive()` would find no backend.
from archivey.formats import directory_reader as _directory_reader  # noqa: F401
