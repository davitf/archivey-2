import copy
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from archivey.config import ArchiveyConfig
from archivey.types import ArchiveFormat, ContainerFormat, MemberType, StreamFormat


class GenerationMethod(Enum):
    ZIPFILE = "zipfile"
    INFOZIP = "infozip"
    TAR_COMMAND_LINE = "tar_cmd"
    TAR_LIBRARY = "tarfile"
    PY7ZR = "py7zr"
    SEVENZIP_COMMAND_LINE = "7z_cmd"
    RAR_COMMAND_LINE = "rar_cmd"
    SINGLE_FILE_COMMAND_LINE = "single_file_cmd"
    SINGLE_FILE_LIBRARY = "single_file_lib"
    ISO_PYCDLIB = "iso_pycdlib"
    ISO_GENISOIMAGE = "iso_genisoimage"
    TEMP_DIR_POPULATION = "temp_dir_population"
    EXTERNAL = "external"
    UNIX_COMPRESS = "unix_compress"


@dataclass
class FileInfo:
    name: str
    mtime: datetime
    contents: bytes | None = None
    password: str | None = None
    comment: str | None = None
    type: MemberType = MemberType.FILE
    link_target: str | None = None
    link_target_type: MemberType | None = MemberType.FILE
    compression_method: str | None = None
    permissions: Optional[int] = None
    uid: int | None = None
    gid: int | None = None
    uname: str | None = None
    gname: str | None = None


def File(
    name: str,
    mtime: datetime | int,
    contents: bytes,
    password: str | None = None,
    comment: str | None = None,
    compression_method: str | None = None,
    permissions: Optional[int] = None,
    uid: int | None = None,
    gid: int | None = None,
    uname: str | None = None,
    gname: str | None = None,
) -> FileInfo:
    if isinstance(mtime, int):
        mtime = _fake_mtime(mtime)
    return FileInfo(
        type=MemberType.FILE,
        name=name,
        mtime=mtime,
        contents=contents,
        password=password,
        comment=comment,
        compression_method=compression_method,
        permissions=permissions,
        uid=uid,
        gid=gid,
        uname=uname,
        gname=gname,
    )


def Dir(
    name: str,
    mtime: datetime | int,
    comment: str | None = None,
    permissions: Optional[int] = None,
) -> FileInfo:
    if isinstance(mtime, int):
        mtime = _fake_mtime(mtime)
    return FileInfo(
        type=MemberType.DIR,
        name=name,
        mtime=mtime,
        comment=comment,
        permissions=permissions,
    )


def Hardlink(
    name: str,
    mtime: datetime | int,
    link_target: str,
    contents: bytes | None = None,
    comment: str | None = None,
    permissions: Optional[int] = None,
) -> FileInfo:
    if isinstance(mtime, int):
        mtime = _fake_mtime(mtime)
    return FileInfo(
        type=MemberType.HARDLINK,
        name=name,
        mtime=mtime,
        contents=contents,
        comment=comment,
        permissions=permissions,
        link_target=link_target,
        link_target_type=MemberType.FILE,
    )


def Symlink(
    name: str,
    mtime: datetime | int,
    link_target: str,
    contents: bytes | None = None,
    comment: str | None = None,
    permissions: Optional[int] = None,
    link_target_type: MemberType | None = MemberType.FILE,
    password: str | None = None,
) -> FileInfo:
    if isinstance(mtime, int):
        mtime = _fake_mtime(mtime)
    return FileInfo(
        type=MemberType.SYMLINK,
        name=name,
        mtime=mtime,
        contents=contents,
        comment=comment,
        permissions=permissions,
        link_target=link_target,
        link_target_type=link_target_type,
        password=password,
    )


@dataclass
class ArchiveContents:
    file_basename: str  # Base name for the archive (e.g., "basic", "encryption")
    files: list[FileInfo]  # List of files to include
    archive_comment: str | None = None  # Optional archive comment
    solid: Optional[bool] = None  # Whether archive should be solid
    header_password: str | None = None  # Optional header password
    generate_corrupted_variants: bool = (
        True  # Whether to generate corrupted variants for testing
    )

    def has_password(self) -> bool:
        return (
            any(f.password is not None for f in self.files)
            or self.header_password is not None
        )

    def has_password_in_files(self) -> bool:
        return any(f.password is not None for f in self.files)

    def has_multiple_passwords(self) -> bool:
        return len({f.password for f in self.files if f.password is not None}) > 1


@dataclass(frozen=True)
class ArchiveFormatFeatures:
    dir_entries: bool = True
    file_comments: bool = False
    archive_comment: bool = False
    mtime: bool = True
    rounded_mtime: bool = False
    file_size: bool = True
    duplicate_files: bool = False
    hardlink_mtime: bool = False
    # A limitation / bug in the RAR4 format: Unicode characters above 0x10000 are not
    # correctly encoded in comment fields.
    comment_corrupts_unicode_non_bmp_chars: bool = False
    mtime_with_tz: bool = False
    link_targets_in_header: bool = True
    replace_backslash_with_slash: bool = False
    ownership: bool = False


DEFAULT_FORMAT_FEATURES = ArchiveFormatFeatures()


@dataclass(frozen=True)
class ArchiveCreationInfo:
    file_suffix: str  # e.g., ".zip", "py7zr.7z", "7zcmd.7z"
    format: ArchiveFormat  # The archive format enum
    generation_method: GenerationMethod  # How to generate the archive
    generation_method_options: dict[str, Any] = field(
        default_factory=dict
    )  # Additional options for generation
    features: ArchiveFormatFeatures = DEFAULT_FORMAT_FEATURES


DEFAULT_ARCHIVES_BASE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "fixtures")
)

TEST_ARCHIVES_DIR = "test_archives"
TEST_ARCHIVES_EXTERNAL_DIR = "test_archives_external"


@dataclass
class SampleArchive:
    # Will be constructed as f"{contents.file_basename}__{format.file_suffix}"
    filename: str

    contents: ArchiveContents
    creation_info: ArchiveCreationInfo
    skip_test: bool = False

    def get_archive_name(self, variant: str | None = None) -> str:
        if variant is None:
            return self.filename
        first_dot = self.filename.find(".")
        if first_dot == -1:
            name = self.filename
            ext = ""
        else:
            name = self.filename[:first_dot]
            ext = self.filename[first_dot:]
        return f"{name}.{variant}{ext}"

    def get_archive_path(
        self, base_dir: str = DEFAULT_ARCHIVES_BASE_DIR, variant: str | None = None
    ) -> str:
        name = self.get_archive_name(variant)
        if self.creation_info.generation_method == GenerationMethod.EXTERNAL:
            return os.path.join(base_dir, TEST_ARCHIVES_EXTERNAL_DIR, name)
        return os.path.join(base_dir, TEST_ARCHIVES_DIR, name)


# Generation method constants
ZIP_ZIPFILE_STORE = ArchiveCreationInfo(
    file_suffix="zipfile_store.zip",
    format=ArchiveFormat.ZIP,
    generation_method=GenerationMethod.ZIPFILE,
    features=ArchiveFormatFeatures(
        file_comments=True,
        archive_comment=True,
        rounded_mtime=True,
        duplicate_files=True,
        mtime_with_tz=False,
        link_targets_in_header=False,
        replace_backslash_with_slash=True,
    ),
    generation_method_options={"compression_method": "store"},
)
ZIP_ZIPFILE_DEFLATE = ArchiveCreationInfo(
    file_suffix="zipfile_deflate.zip",
    format=ArchiveFormat.ZIP,
    generation_method=GenerationMethod.ZIPFILE,
    features=ArchiveFormatFeatures(
        file_comments=True,
        archive_comment=True,
        rounded_mtime=True,
        duplicate_files=True,
        mtime_with_tz=False,
        link_targets_in_header=False,
        replace_backslash_with_slash=True,
    ),
    generation_method_options={"compression_method": "deflate"},
)
ZIP_INFOZIP = ArchiveCreationInfo(
    file_suffix="infozip.zip",
    format=ArchiveFormat.ZIP,
    generation_method=GenerationMethod.INFOZIP,
    # Times are not rounded, as infozip adds the timestamps extra field
    features=ArchiveFormatFeatures(
        file_comments=True,
        archive_comment=True,
        rounded_mtime=False,
        # Info-zip files include the extended timestamp extra field, which store
        # times in UTC.
        mtime_with_tz=True,
        link_targets_in_header=False,
        replace_backslash_with_slash=True,
    ),
)

# 7z formats
SEVENZIP_PY7ZR = ArchiveCreationInfo(
    file_suffix="py7zr.7z",
    format=ArchiveFormat.SEVENZIP,
    generation_method=GenerationMethod.PY7ZR,
    features=ArchiveFormatFeatures(
        dir_entries=False,
        archive_comment=True,
        duplicate_files=True,
        mtime_with_tz=True,
        link_targets_in_header=False,
        replace_backslash_with_slash=True,
    ),
)
SEVENZIP_7ZCMD = ArchiveCreationInfo(
    file_suffix="7zcmd.7z",
    format=ArchiveFormat.SEVENZIP,
    generation_method=GenerationMethod.SEVENZIP_COMMAND_LINE,
    features=ArchiveFormatFeatures(
        dir_entries=False,
        archive_comment=True,
        mtime_with_tz=True,
        link_targets_in_header=False,
        # This is not a limitation in 7z, but a behavior of py7zr: it replaces
        # backslashes with slashes in the file names
        # (see FilesInfo::_read_name in py7zr/archiveinfo.py)
        replace_backslash_with_slash=True,
    ),
)

# RAR format
RAR_CMD = ArchiveCreationInfo(
    file_suffix=".rar",
    format=ArchiveFormat.RAR,
    generation_method=GenerationMethod.RAR_COMMAND_LINE,
    features=ArchiveFormatFeatures(
        dir_entries=True, archive_comment=True, mtime_with_tz=True
    ),
)
RAR4_CMD = ArchiveCreationInfo(
    file_suffix="rar4.rar",
    format=ArchiveFormat.RAR,
    generation_method=GenerationMethod.RAR_COMMAND_LINE,
    generation_method_options={"rar4_format": True},
    features=ArchiveFormatFeatures(
        dir_entries=True,
        archive_comment=True,
        comment_corrupts_unicode_non_bmp_chars=True,
        mtime_with_tz=False,
        link_targets_in_header=False,
        replace_backslash_with_slash=True,
    ),
)

_TAR_FORMAT_FEATURES_TARCMD = ArchiveFormatFeatures(mtime_with_tz=True, ownership=True)
_TAR_FORMAT_FEATURES_TARFILE = ArchiveFormatFeatures(
    duplicate_files=True, hardlink_mtime=True, mtime_with_tz=True, ownership=True
)

# TAR formats
TAR_PLAIN_CMD = ArchiveCreationInfo(
    file_suffix="tarcmd.tar",
    format=ArchiveFormat.TAR,
    generation_method=GenerationMethod.TAR_COMMAND_LINE,
    features=_TAR_FORMAT_FEATURES_TARCMD,
)

# TAR formats
TAR_PLAIN_TARFILE = ArchiveCreationInfo(
    file_suffix="tarfile.tar",
    format=ArchiveFormat.TAR,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)

TAR_GZ_CMD = ArchiveCreationInfo(
    file_suffix="tarcmd.tar.gz",
    format=ArchiveFormat.TAR_GZ,
    generation_method=GenerationMethod.TAR_COMMAND_LINE,
    features=_TAR_FORMAT_FEATURES_TARCMD,
)
TAR_GZ_TARFILE = ArchiveCreationInfo(
    file_suffix="tarfile.tar.gz",
    format=ArchiveFormat.TAR_GZ,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_ZSTD_CMD = ArchiveCreationInfo(
    file_suffix="tarcmd.tar.zst",
    format=ArchiveFormat.TAR_ZSTD,
    generation_method=GenerationMethod.TAR_COMMAND_LINE,
    features=_TAR_FORMAT_FEATURES_TARCMD,
)
TAR_ZSTD_TARFILE = ArchiveCreationInfo(
    file_suffix="tarfile.tar.zst",
    format=ArchiveFormat.TAR_ZSTD,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_Z_CMD = ArchiveCreationInfo(
    file_suffix="tarcmd.tar.Z",
    format=ArchiveFormat.TAR_Z,
    generation_method=GenerationMethod.TAR_COMMAND_LINE,
    features=_TAR_FORMAT_FEATURES_TARCMD,
)


# No need to test both tarfile and cmdline for the other formats, as there shouldn't
# be significant differences that won't be caught by the gz format.
TAR_BZ2 = ArchiveCreationInfo(
    file_suffix=".tar.bz2",
    format=ArchiveFormat.TAR_BZ2,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_XZ = ArchiveCreationInfo(
    file_suffix=".tar.xz",
    format=ArchiveFormat.TAR_XZ,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_LZ4 = ArchiveCreationInfo(
    file_suffix=".tar.lz4",
    format=ArchiveFormat.TAR_LZ4,
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_LZIP = ArchiveCreationInfo(
    file_suffix=".tar.lz",
    format=ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP),
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_BROTLI = ArchiveCreationInfo(
    file_suffix=".tar.br",
    format=ArchiveFormat(ContainerFormat.TAR, StreamFormat.BROTLI),
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)
TAR_ZLIB = ArchiveCreationInfo(
    file_suffix=".tar.zz",
    format=ArchiveFormat(ContainerFormat.TAR, StreamFormat.ZLIB),
    generation_method=GenerationMethod.TAR_LIBRARY,
    features=_TAR_FORMAT_FEATURES_TARFILE,
)

# Single file compression formats
GZIP_CMD = ArchiveCreationInfo(
    file_suffix="cmd.gz",
    format=ArchiveFormat.GZIP,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    # Dp not preserve filename and timestamp
    generation_method_options={"compression_cmd": "gzip", "cmd_args": ["-n"]},
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
GZIP_CMD_PRESERVE_METADATA = ArchiveCreationInfo(
    file_suffix="cmd.gz",
    format=ArchiveFormat.GZIP,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "gzip", "cmd_args": ["-N"]},
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
UNIX_COMPRESS_CMD = ArchiveCreationInfo(
    file_suffix="cmd.Z",
    format=ArchiveFormat.UNIX_COMPRESS,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "compress", "cmd_args": ["-f"]},
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)

BZIP2_CMD = ArchiveCreationInfo(
    file_suffix="cmd.bz2",
    format=ArchiveFormat.BZIP2,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "bzip2"},
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
XZ_CMD = ArchiveCreationInfo(
    file_suffix="cmd.xz",
    format=ArchiveFormat.XZ,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "xz"},
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
ZSTD_CMD = ArchiveCreationInfo(
    file_suffix="cmd.zst",
    format=ArchiveFormat.ZSTD,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "zstd"},
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
LZ4_CMD = ArchiveCreationInfo(
    file_suffix="cmd.lz4",
    format=ArchiveFormat.LZ4,
    generation_method=GenerationMethod.SINGLE_FILE_COMMAND_LINE,
    generation_method_options={"compression_cmd": "lz4"},
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)

GZIP_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.gz",
    format=ArchiveFormat.GZIP,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    generation_method_options={"opener_kwargs": {"mtime": 0}},
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
BZIP2_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.bz2",
    format=ArchiveFormat.BZIP2,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
XZ_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.xz",
    format=ArchiveFormat.XZ,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
ZSTD_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.zst",
    format=ArchiveFormat.ZSTD,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
LZ4_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.lz4",
    format=ArchiveFormat.LZ4,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
LZIP_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.lz",
    format=ArchiveFormat.LZIP,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=True, mtime_with_tz=True),
)
ZLIB_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.zz",
    format=ArchiveFormat.ZLIB,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)
BROTLI_LIBRARY = ArchiveCreationInfo(
    file_suffix="lib.br",
    format=ArchiveFormat.BROTLI,
    generation_method=GenerationMethod.SINGLE_FILE_LIBRARY,
    features=ArchiveFormatFeatures(file_size=False, mtime_with_tz=True),
)

FOLDER_FORMAT = ArchiveCreationInfo(
    file_suffix="_folder/",  # Results in names like 'basic_nonsolid___folder'
    format=ArchiveFormat.FOLDER,
    generation_method=GenerationMethod.TEMP_DIR_POPULATION,
    features=ArchiveFormatFeatures(  # Specify features relevant to folders
        dir_entries=True,
        file_comments=False,
        archive_comment=False,
        mtime=True,
        rounded_mtime=False,  # Filesystem mtimes are usually not rounded
        file_size=True,
        duplicate_files=False,
        mtime_with_tz=True,
        hardlink_mtime=False,  # Hardlinks get the timestamp of the original file
    ),
)
# ISO 9660 format
# Note: mtime=False because pycdlib's add_file/add_directory does not copy the
# source file's mtime into the directory record date; it uses the current time.
# Rock Ridge and Joliet are enabled (interchange_level=3, rock_ridge="1.09", joliet=3).
_ISO_FORMAT_FEATURES = ArchiveFormatFeatures(
    dir_entries=True,
    file_comments=False,
    archive_comment=False,
    mtime=False,
    mtime_with_tz=False,
    rounded_mtime=False,
    file_size=True,
    link_targets_in_header=False,
    ownership=False,
)
ISO_PYCDLIB = ArchiveCreationInfo(
    file_suffix="pycdlib.iso",
    format=ArchiveFormat.ISO,
    generation_method=GenerationMethod.ISO_PYCDLIB,
    features=_ISO_FORMAT_FEATURES,
)
ISO_GENISOIMAGE = ArchiveCreationInfo(
    file_suffix="genisoimage.iso",
    format=ArchiveFormat.ISO,
    generation_method=GenerationMethod.ISO_GENISOIMAGE,
    features=_ISO_FORMAT_FEATURES,
)

ALL_SINGLE_FILE_FORMATS = [
    GZIP_CMD,
    BZIP2_CMD,
    XZ_CMD,
    ZSTD_CMD,
    LZ4_CMD,
    UNIX_COMPRESS_CMD,
    GZIP_LIBRARY,
    BZIP2_LIBRARY,
    XZ_LIBRARY,
    ZSTD_LIBRARY,
    LZ4_LIBRARY,
    ZLIB_LIBRARY,
    BROTLI_LIBRARY,
    LZIP_LIBRARY,
]

BASIC_TAR_FORMATS = [
    TAR_PLAIN_CMD,
    TAR_PLAIN_TARFILE,
    TAR_GZ_CMD,
    TAR_GZ_TARFILE,
    TAR_ZSTD_CMD,
    TAR_ZSTD_TARFILE,
]

ALL_TAR_FORMATS = BASIC_TAR_FORMATS + [
    TAR_BZ2,
    TAR_XZ,
    TAR_LZ4,
    TAR_LZIP,
    TAR_Z_CMD,
    TAR_BROTLI,
    TAR_ZLIB,
]

LARGE_TAR_FORMATS = ALL_TAR_FORMATS

ZIP_FORMATS = [
    ZIP_ZIPFILE_STORE,
    ZIP_ZIPFILE_DEFLATE,
    ZIP_INFOZIP,
]

RAR_FORMATS = [
    RAR_CMD,
    # Causing several test failures, will need to investigate
    RAR4_CMD,
]

SEVENZIP_FORMATS = [
    SEVENZIP_PY7ZR,
    SEVENZIP_7ZCMD,
]

ISO_FORMATS = [
    ISO_PYCDLIB,
    ISO_GENISOIMAGE,
]

ZIP_RAR_7Z_FORMATS = ZIP_FORMATS + RAR_FORMATS + SEVENZIP_FORMATS

# Skip test filenames
SKIP_TEST_FILENAMES = {
    "single_file__lib.br",
    "large_single_file__lib.br",
}


def _create_random_data(size: int, seed: int, chars: bytes = b"0123456789 ") -> bytes:
    r = random.Random(seed)
    memview = memoryview(bytearray(size))
    for i in range(size):
        memview[i] = r.choice(chars)
    return memview.tobytes()


def _fake_mtime(i: int) -> datetime:
    def _mod_1(i: int, mod: int) -> int:
        return (i - 1) % mod + 1

    if i == 0:
        return datetime(1980, 1, 1, 0, 0, 0)

    return datetime(
        2000 + i, _mod_1(i, 12), _mod_1(i, 28), i % 24, (i + 1) % 60, (i + 2) % 60
    )


BASIC_FILES = [
    # Use odd seconds to test that the ZIP extended timestamp is being read correctly
    # (as the standard timestamp is rounded to the nearest 2 seconds)
    File("file1.txt", 1, b"Hello, world!"),
    Dir("subdir/", 2),
    File("empty_file.txt", 3, b""),
    Dir("empty_subdir/", 4),
    File("subdir/file2.txt", 5, b"Hello, universe!"),
    File("implicit_subdir/file3.txt", 6, b"Hello there!"),
]


COMMENT_FILES = [
    File("abc.txt", 1, b"ABC", comment="Contains some letters"),
    Dir("subdir/", 7, comment="Contains some files"),
    File("subdir/123.txt", 8, b"1234567890", comment="Contains some numbers"),
]

ENCRYPTION_SEVERAL_PASSWORDS_FILES = [
    File("plain.txt", 1, b"This is plain"),
    # For 7zip archives to be considered solid, they need to have at least two files
    # in the same folder. To make that possible, we need two consecutive files with the
    # same password.
    File("secret.txt", 2, b"This is secret", password="password"),
    File("also_secret.txt", 3, b"This is also secret", password="password"),
    File(
        "not_secret.txt", 4, b"This is not secret", comment="Contains some information"
    ),
    File(
        "very_secret.txt",
        5,
        b"This is very secret",
        password="very_secret_password",
        comment="Contains some very secret information",
    ),
]

ENCRYPTION_SEVERAL_PASSWORDS_AND_SYMLINKS_FILES = [
    File("plain.txt", 1, b"This is plain"),
    # For 7zip archives to be considered solid, they need to have at least two files
    # in the same folder. To make that possible, we need two consecutive files with the
    # same password.
    File("secret.txt", 2, b"Secret", password="pwd"),
    File("also_secret.txt", 3, b"Also secret", password="pwd"),
    Symlink(
        "encrypted_link_to_secret.txt",
        4,
        "secret.txt",
        contents=b"Secret",
        password="pwd",
    ),
    Symlink(
        "encrypted_link_to_very_secret.txt",
        5,
        "very_secret.txt",
        contents=b"Very secret",
        password="pwd",
    ),
    Symlink(
        "encrypted_link_to_not_secret.txt",
        6,
        "not_secret.txt",
        contents=b"Not secret",
        password="longpwd",
    ),
    Symlink("plain_link_to_secret.txt", 7, "secret.txt", contents=b"Secret"),
    File("not_secret.txt", 6, b"Not secret"),
    File("very_secret.txt", 7, b"Very secret", password="longpwd"),
]

ENCRYPTION_SINGLE_PASSWORD_FILES = [
    File("secret.txt", 1, b"This is secret", password="password"),
    File("also_secret.txt", 2, b"This is also secret", password="password"),
]

ENCRYPTION_ENCRYPTED_AND_PLAIN_FILES = ENCRYPTION_SINGLE_PASSWORD_FILES + [
    File("not_secret.txt", 3, b"This is not secret"),
]

SYMLINKS_FILES = [
    File("file1.txt", 1, b"Hello, world!"),
    Symlink("symlink_to_file1.txt", 2, "file1.txt", contents=b"Hello, world!"),
    Dir("subdir/", 3),
    Symlink("subdir/link_to_file1.txt", 4, "../file1.txt", contents=b"Hello, world!"),
    Symlink("subdir_link", 5, "subdir", link_target_type=MemberType.DIR),
    Symlink("subdir_link_with_slash", 5, "subdir/", link_target_type=MemberType.DIR),
]

SYMLINK_LOOP_FILES = [
    Symlink("file1.txt", 1, "file2.txt"),
    Symlink("file2.txt", 2, "file3.txt"),
    Symlink("file3.txt", 3, "file1.txt"),
    Symlink("file4.txt", 4, "file5.txt", contents=b"this is file 5"),
    File("file5.txt", 5, b"this is file 5"),
]

HARDLINKS_FILES = [
    File("file1.txt", 1, b"Hello 1!"),
    File("subdir/file2.txt", 2, b"Hello 2!"),
    Hardlink("subdir/hardlink_to_file1.txt", 3, "file1.txt", contents=b"Hello 1!"),
    Hardlink("hardlink_to_file2.txt", 4, "subdir/file2.txt", contents=b"Hello 2!"),
]

HARDLINKS_FILES_FOR_FOLDER_FORMAT = [
    File("file1.txt", 1, b"Hello 1!"),
    File("dir1/file2.txt", 2, b"Hello 2!"),
    Hardlink("dir1/link_to_file1.txt", 3, "file1.txt", contents=b"Hello 1!"),
    Hardlink("dir1/link_to_file2.txt", 4, "dir1/file2.txt", contents=b"Hello 2!"),
    Hardlink("dir2/hardlink_to_file1.txt", 4, "file1.txt", contents=b"Hello 1!"),
    Hardlink("dir2/hardlink_to_file2.txt", 5, "dir1/file2.txt", contents=b"Hello 2!"),
]


# In tar archives, a hard link refers to the entry with the same name previously in
# the archive, even if that entry is later overwritten. So in this case, the first
# hard link should refer to the first file version, and the second hard link should
# refer to the second file version.
HARDLINKS_WITH_DUPLICATE_FILES = [
    File("file1.txt", 1, b"Old contents"),
    Hardlink("hardlink_to_file1_old.txt", 2, "file1.txt", contents=b"Old contents"),
    File("file1.txt", 3, b"New contents!"),
    Hardlink("hardlink_to_file1_new.txt", 4, "file1.txt", contents=b"New contents!"),
    File("file1.txt", 5, b"Newer contents!!"),
]

HARDLINKS_RECURSIVE_AND_BROKEN = [
    File("a_file.txt", 1, b"Hello!"),
    Hardlink("b_broken_forward_hardlink.txt", 2, "d_hardlink.txt"),
    Symlink("c_forward_symlink.txt", 3, "d_hardlink.txt", contents=b"Hello!"),
    Hardlink("d_hardlink.txt", 4, "a_file.txt", contents=b"Hello!"),
    Hardlink("e_double_hardlink.txt", 5, "d_hardlink.txt", contents=b"Hello!"),
    Hardlink("f_hardlink_to_broken.txt", 6, "b_broken_forward_hardlink.txt"),
    Symlink("g_symlink_to_broken.txt", 7, "b_broken_forward_hardlink.txt"),
    # Sometimes tar files can contain hardlinks to the same file (particularly if we
    # call tar with the filename twice in the command line)
    Hardlink("a_file.txt", 8, "a_file.txt", contents=b"Hello!"),
]


ENCODING_FILES = [
    File("Español.txt", 1, b"Hola, mundo!"),
    File("Català.txt", 1, "Hola, món!".encode("utf-8")),
    File("Português.txt", 1, "Olá, mundo!".encode("utf-8")),
    File("emoji_😀.txt", 1, b"I'm happy"),
]

COMPRESSION_METHODS_FILES = [
    File("store.txt", 1, b"I am stored\n" * 1000, compression_method="store"),
    File("deflate.txt", 2, b"I am deflated\n" * 1000, compression_method="deflate"),
    File("bzip2.txt", 3, b"I am bzip'd\n" * 1000, compression_method="bzip2"),
]

COMPRESSION_METHOD_FILES_LZMA = COMPRESSION_METHODS_FILES + [
    File("lzma.txt", 4, b"I am lzma'd\n" * 1000, compression_method="lzma"),
]

MARKER_FILENAME_BASED_ON_ARCHIVE_NAME = "SINGLE_FILE_MARKER"
MARKER_MTIME_BASED_ON_ARCHIVE_NAME = datetime(3141, 5, 9, 2, 6, 53, tzinfo=timezone.utc)

# Single compressed files (e.g. .gz, .bz2, .xz)
SINGLE_FILE_TXT_CONTENT = b"This is a single test file for compression.\n"
SINGLE_FILE_INFO_FIXED_FILENAME_AND_MTIME = File(
    "single_file_fixed.txt", 1, SINGLE_FILE_TXT_CONTENT
)
SINGLE_FILE_INFO_NO_METADATA = File(
    MARKER_FILENAME_BASED_ON_ARCHIVE_NAME,
    MARKER_MTIME_BASED_ON_ARCHIVE_NAME,
    SINGLE_FILE_TXT_CONTENT,
)

TEST_PERMISSIONS_FILES = [
    File("standard.txt", 1, b"Standard permissions.", permissions=0o644),
    File("readonly.txt", 2, b"Read-only permissions.", permissions=0o444),
    File(
        "executable.sh",
        3,
        b"#!/bin/sh\necho 'Executable permissions.'",
        permissions=0o755,
    ),
    File("world_readable.txt", 4, b"World readable permissions.", permissions=0o666),
]

LARGE_FILES = [
    File(
        f"large{i}.txt",
        i,
        f"Large file #{i}\n".encode() + _create_random_data(200000, i),
    )
    for i in range(1, 6)
]

# Files with potentially unsafe names or permissions for filter testing
SANITIZE_FILES_WITHOUT_ABSOLUTE_PATHS = [
    File("good.txt", 1, b"good", uid=1001, gid=1002, uname="the_user", gname="a_group"),
    File("exec.sh", 4, b"#!/bin/sh\n", permissions=0o755),
    Symlink("subdir/good_link.txt", 5, "../good.txt", contents=b"good"),
    Symlink("link_abs", 6, "/etc/passwd", contents=None),
    Symlink("link_outside", 7, "../escape.txt", contents=None),
    File("backslash/..\\good.txt", 10, b"not the same as good.txt"),
]

# Files with potentially unsafe names or permissions for filter testing
SANITIZE_FILES_WITHOUT_HARDLINKS = [
    File("good.txt", 1, b"good", uid=1001, gid=1002, uname="the_user", gname="a_group"),
    File("/absfile.txt", 2, b"abs"),
    File("../outside.txt", 3, b"outside"),
    File("exec.sh", 4, b"#!/bin/sh\n", permissions=0o755),
    Symlink("subdir/good_link.txt", 5, "../good.txt", contents=b"good"),
    Symlink("link_abs", 6, "/etc/passwd", contents=None),
    Symlink("link_outside", 7, "../escape.txt", contents=None),
    File("backslash/..\\good.txt", 10, b"not the same as good.txt"),
]


# Files with potentially unsafe names or permissions for filter testing
SANITIZE_FILES_FULL = [
    File("good.txt", 1, b"good", uid=1001, gid=1002, uname="the_user", gname="a_group"),
    File("/absfile.txt", 2, b"abs"),
    File("C:/windows_absfile.txt", 2, b"abs"),
    File("../outside.txt", 3, b"outside"),
    File("exec.sh", 4, b"#!/bin/sh\n", permissions=0o755),
    Symlink("subdir/good_link.txt", 5, "../good.txt", contents=b"good"),
    Symlink("link_abs", 6, "/etc/passwd", contents=None),
    Symlink("link_outside", 7, "../escape.txt", contents=None),
    Hardlink("hardlink_absfile", 8, "/absfile.txt", contents=b"abs"),
    Hardlink("hardlink_outside", 9, "../outside.txt", contents=b"outside"),
    File("backslash/..\\good.txt", 10, b"not the same as good.txt"),
]

SINGLE_LARGE_FILE = File(
    MARKER_FILENAME_BASED_ON_ARCHIVE_NAME,
    MARKER_MTIME_BASED_ON_ARCHIVE_NAME,
    _create_random_data(1000000, 1),
)

DUPLICATE_FILES = [
    File("file1.txt", 1, b"Old contents"),  # len: 12, CRC: e8c902a6
    File("file2.txt", 2, b"Duplicate contents"),
    File("file1.txt", 3, b"New contents!"),  # len: 13, CRC: d61d71d2
    # Might get turned into a link or reference
    File("file2_dupe.txt", 4, b"Duplicate contents"),
]


def build_archive_infos(
    contents: ArchiveContents,
    format_infos: list[ArchiveCreationInfo],
) -> list[SampleArchive]:
    """Build all ArchiveInfo objects from the definitions."""
    archives = []
    for format_info in format_infos:
        filename = f"{contents.file_basename}__{format_info.file_suffix}"
        archive_info = SampleArchive(
            filename=filename,
            contents=contents,
            creation_info=format_info,
            skip_test=filename in SKIP_TEST_FILENAMES,
        )

        if any(
            MARKER_FILENAME_BASED_ON_ARCHIVE_NAME in a.name
            for a in archive_info.contents.files
        ):
            archive_info.contents = copy.deepcopy(archive_info.contents)
            for file in archive_info.contents.files:
                if file.name == MARKER_FILENAME_BASED_ON_ARCHIVE_NAME:
                    archive_name_without_ext = os.path.splitext(archive_info.filename)[
                        0
                    ]
                    file.name = archive_name_without_ext

        archives.append(archive_info)
    return archives


def filter_archives(
    archives: list[SampleArchive],
    prefixes: list[str] | None = None,
    extensions: list[str] | None = None,
    custom_filter: Callable[[SampleArchive], bool] | None = None,
) -> list[SampleArchive]:
    """Filter archives by filename prefixes and/or extensions."""

    if prefixes:
        filtered = []
        for prefix in prefixes:
            prefix_found = False
            for a in archives:
                if a.filename.startswith(prefix + "__"):
                    filtered.append(a)
                    prefix_found = True
            if not prefix_found:
                raise ValueError(f"No archives match prefix {prefix}")
    else:
        filtered = archives

    if extensions:
        filtered = [
            a for a in filtered if any(a.filename.endswith(e) for e in extensions)
        ]

    if custom_filter:
        filtered = [a for a in filtered if custom_filter(a)]

    if not filtered:
        raise ValueError("No archives match the filter criteria")

    return filtered


BASIC_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="basic_nonsolid",
        files=BASIC_FILES,
    ),
    ZIP_RAR_7Z_FORMATS + [FOLDER_FORMAT],
) + build_archive_infos(
    ArchiveContents(
        file_basename="basic_solid",
        files=BASIC_FILES,
        solid=True,
    ),
    RAR_FORMATS + SEVENZIP_FORMATS + ALL_TAR_FORMATS,
)

COMMENT_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="comment",
        files=COMMENT_FILES,
        archive_comment="This is a\nmulti-line comment",
    ),
    ZIP_FORMATS + RAR_FORMATS,
)

ENCRYPTION_ARCHIVES = (
    build_archive_infos(
        ArchiveContents(
            file_basename="encryption",
            files=ENCRYPTION_SINGLE_PASSWORD_FILES,
            solid=False,
        ),
        [ZIP_INFOZIP] + RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encryption_several_passwords",
            files=ENCRYPTION_SEVERAL_PASSWORDS_FILES,
            solid=False,
        ),
        [ZIP_INFOZIP] + RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encryption_with_plain",
            files=ENCRYPTION_ENCRYPTED_AND_PLAIN_FILES,
            solid=False,
        ),
        [ZIP_INFOZIP] + RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encryption_solid",
            files=ENCRYPTION_SINGLE_PASSWORD_FILES,
            solid=True,
        ),
        RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encrypted_header",
            files=BASIC_FILES,
            header_password="header_password",
        ),
        RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encrypted_header_solid",
            files=BASIC_FILES,
            solid=True,
            header_password="header_password",
        ),
        RAR_FORMATS + SEVENZIP_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="encryption_with_symlinks",
            files=ENCRYPTION_SEVERAL_PASSWORDS_AND_SYMLINKS_FILES,
            solid=False,
        ),
        RAR_FORMATS + [SEVENZIP_7ZCMD],
    )
)

SYMLINK_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="symlinks",
        files=SYMLINKS_FILES,
        solid=False,
    ),
    ZIP_RAR_7Z_FORMATS + [FOLDER_FORMAT],
) + build_archive_infos(
    ArchiveContents(
        file_basename="symlinks_solid",
        files=SYMLINKS_FILES,
        solid=True,
    ),
    RAR_FORMATS + SEVENZIP_FORMATS + ALL_TAR_FORMATS,
)

HARDLINK_ARCHIVES = (
    build_archive_infos(
        ArchiveContents(
            file_basename="hardlinks_nonsolid",
            files=HARDLINKS_FILES,
        ),
        [RAR_CMD],  # RAR4 does not support hardlinks
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="hardlinks_solid",
            files=HARDLINKS_FILES,
            solid=True,
        ),
        [RAR_CMD] + BASIC_TAR_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="hardlinks_folder_format",
            files=HARDLINKS_FILES_FOR_FOLDER_FORMAT,
        ),
        [FOLDER_FORMAT],
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="hardlinks_with_duplicate_files",
            files=HARDLINKS_WITH_DUPLICATE_FILES,
        ),
        [TAR_PLAIN_TARFILE],  # , TAR_GZ_TARFILE],
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="hardlinks_recursive_and_broken",
            files=HARDLINKS_RECURSIVE_AND_BROKEN,
        ),
        [TAR_PLAIN_TARFILE, TAR_GZ_TARFILE],
    )
)

ENCODING_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="encoding",
        files=ENCODING_FILES,
    ),
    ZIP_RAR_7Z_FORMATS + [FOLDER_FORMAT] + BASIC_TAR_FORMATS,
) + build_archive_infos(
    ArchiveContents(
        file_basename="encoding_comment",
        files=ENCODING_FILES,
        archive_comment="Comentário em português 😀",
    ),
    ZIP_FORMATS + RAR_FORMATS,
)


COMPRESSION_METHODS_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="compression_methods",
        files=COMPRESSION_METHODS_FILES,
    ),
    ZIP_FORMATS,
) + build_archive_infos(
    ArchiveContents(
        file_basename="compression_methods_lzma",
        files=COMPRESSION_METHOD_FILES_LZMA,
    ),
    [ZIP_ZIPFILE_STORE],  # Infozip doesn't support lzma
)

SINGLE_FILE_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="single_file_with_metadata",
        files=[SINGLE_FILE_INFO_FIXED_FILENAME_AND_MTIME],
    ),
    [GZIP_CMD_PRESERVE_METADATA],
) + build_archive_infos(
    ArchiveContents(
        file_basename="single_file",
        files=[SINGLE_FILE_INFO_NO_METADATA],
    ),
    ALL_SINGLE_FILE_FORMATS,
)

PERMISSIONS_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="permissions",
        files=TEST_PERMISSIONS_FILES,
    ),
    ZIP_RAR_7Z_FORMATS + [FOLDER_FORMAT] + BASIC_TAR_FORMATS,
)

LARGE_ARCHIVES = (
    build_archive_infos(
        ArchiveContents(
            file_basename="large_files_nonsolid",
            files=LARGE_FILES,
        ),
        ZIP_RAR_7Z_FORMATS + [FOLDER_FORMAT],
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="large_files_solid",
            files=LARGE_FILES,
            solid=True,
        ),
        RAR_FORMATS + SEVENZIP_FORMATS + LARGE_TAR_FORMATS,
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="large_single_file",
            files=[SINGLE_LARGE_FILE],
        ),
        ALL_SINGLE_FILE_FORMATS,
    )
)

SYMLINK_LOOP_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="symlink_loop",
        files=SYMLINK_LOOP_FILES,
    ),
    [ZIP_INFOZIP, TAR_PLAIN_TARFILE, FOLDER_FORMAT],
)

DUPLICATE_FILES_ARCHIVES = build_archive_infos(
    ArchiveContents(
        file_basename="duplicate_files",
        files=DUPLICATE_FILES,
    ),
    ZIP_RAR_7Z_FORMATS + [TAR_PLAIN_TARFILE, TAR_GZ_TARFILE],
)

SANITIZE_ARCHIVES = (
    build_archive_infos(
        ArchiveContents(
            file_basename="sanitize",
            files=SANITIZE_FILES_FULL,
        ),
        [TAR_PLAIN_TARFILE, TAR_GZ_TARFILE],
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="sanitize",
            files=SANITIZE_FILES_WITHOUT_HARDLINKS,
        ),
        [ZIP_ZIPFILE_STORE],
    )
    + build_archive_infos(
        ArchiveContents(
            file_basename="sanitize",
            files=SANITIZE_FILES_WITHOUT_ABSOLUTE_PATHS,
        ),
        [SEVENZIP_7ZCMD] + RAR_FORMATS,
    )
)

ALTERNATIVE_CONFIG = ArchiveyConfig(
    use_rapidgzip=True,
    use_indexed_bzip2=True,
    use_zstandard=True,
    use_python_xz=True,
)

ALTERNATIVE_PACKAGES_FORMATS = (
    ArchiveFormat.GZIP,
    ArchiveFormat.BZIP2,
    ArchiveFormat.XZ,
    ArchiveFormat.ZSTD,
    ArchiveFormat.TAR_GZ,
    ArchiveFormat.TAR_BZ2,
    ArchiveFormat.TAR_XZ,
    ArchiveFormat.TAR_ZSTD,
)

SAMPLE_ARCHIVES = (
    BASIC_ARCHIVES
    + COMMENT_ARCHIVES
    + ENCRYPTION_ARCHIVES
    + SYMLINK_ARCHIVES
    + HARDLINK_ARCHIVES
    + ENCODING_ARCHIVES
    + COMPRESSION_METHODS_ARCHIVES
    + SINGLE_FILE_ARCHIVES
    + PERMISSIONS_ARCHIVES
    + LARGE_ARCHIVES
    + SYMLINK_LOOP_ARCHIVES
    + DUPLICATE_FILES_ARCHIVES
    + SANITIZE_ARCHIVES
)

# Verify all skip test filenames were created
created_filenames = {a.filename for a in SAMPLE_ARCHIVES}
missing_skip_tests = SKIP_TEST_FILENAMES - created_filenames
if missing_skip_tests:
    raise ValueError(
        f"Some skip test filenames were not created: {missing_skip_tests}. Created filenames: {created_filenames}"
    )


if __name__ == "__main__":
    # Check if the files in tests/test_archives matches the list in SAMPLE_ARCHIVES.

    expected_files = {
        a.filename
        for a in SAMPLE_ARCHIVES
        if a.creation_info.format != ArchiveFormat.FOLDER
    }
    existing_files = set(
        os.listdir(os.path.join(DEFAULT_ARCHIVES_BASE_DIR, TEST_ARCHIVES_DIR))
    )
    missing_files = expected_files - existing_files
    extra_files = existing_files - expected_files

    if missing_files:
        print(f"Files missing in {DEFAULT_ARCHIVES_BASE_DIR}/{TEST_ARCHIVES_DIR}:")
        for filename in sorted(missing_files):
            print(f"    {filename}")

    if extra_files:
        print(
            f"Files in {DEFAULT_ARCHIVES_BASE_DIR}/{TEST_ARCHIVES_DIR} but not in SAMPLE_ARCHIVES:"
        )
        for filename in sorted(extra_files):
            print(f"    {filename}")

    if not missing_files and not extra_files:
        print("All files match")
    else:
        sys.exit(1)
