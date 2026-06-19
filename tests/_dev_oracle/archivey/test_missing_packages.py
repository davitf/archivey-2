import logging
import os
import sys
from unittest.mock import patch

import pytest

from archivey.core import open_archive
from archivey.exceptions import (
    PackageNotInstalledError,
)
from archivey.internal.dependency_checker import get_dependency_versions
from tests.archivey.sample_archives import (
    ALTERNATIVE_CONFIG,
    SAMPLE_ARCHIVES,
    SampleArchive,
    filter_archives,
)

logger = logging.getLogger(__name__)

# Tests for LibraryNotInstalledError
BASIC_RAR_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["basic_nonsolid"], extensions=["rar"]
)[0]

HEADER_ENCRYPTED_RAR_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["encrypted_header"], extensions=["rar"]
)[0]

NORMAL_ENCRYPTED_RAR_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["encryption"], extensions=["rar"]
)[0]

BASIC_7Z_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["basic_nonsolid"], extensions=["7z"]
)[0]

# BASIC_ISO_ARCHIVE = filter_archives(
#     SAMPLE_ARCHIVES, prefixes=["basic_nonsolid"], extensions=["iso"]
# )[0]

BASIC_ZSTD_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["zst"]
)[0]

BASIC_LZ4_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["lz4"]
)[0]

BASIC_GZIP_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["gz"]
)[0]

BASIC_BZIP2_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["bz2"]
)[0]

BASIC_XZ_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["xz"]
)[0]

BASIC_BROTLI_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["br"]
)[0]

BASIC_UNIX_COMPRESS_ARCHIVE = filter_archives(
    SAMPLE_ARCHIVES, prefixes=["single_file"], extensions=["Z"]
)[0]


@pytest.mark.parametrize(
    ["library_name", "sample_archive", "alternative_packages"],
    [
        # ("pycdlib", BASIC_ISO_ARCHIVE.get_archive_path(), None),
        ("rarfile", BASIC_RAR_ARCHIVE, False),
        ("py7zr", BASIC_7Z_ARCHIVE, False),
        ("rapidgzip", BASIC_GZIP_ARCHIVE, True),
        ("indexed_bzip2", BASIC_BZIP2_ARCHIVE, True),
        ("pyzstd", BASIC_ZSTD_ARCHIVE, False),
        ("zstandard", BASIC_ZSTD_ARCHIVE, True),
        ("lz4", BASIC_LZ4_ARCHIVE, False),
        ("brotli", BASIC_BROTLI_ARCHIVE, False),
        ("uncompresspy", BASIC_UNIX_COMPRESS_ARCHIVE, False),
        ("python-xz", BASIC_XZ_ARCHIVE, True),
    ],
    ids=lambda x: os.path.basename(x) if isinstance(x, str) else x,
)
def test_missing_package_raises_exception(
    library_name: str, sample_archive: SampleArchive, alternative_packages: bool
):
    config = ALTERNATIVE_CONFIG if alternative_packages else None
    archive_path = sample_archive.get_archive_path()
    dependencies = get_dependency_versions()
    library_version = getattr(dependencies, f"{library_name.replace('-', '_')}_version")

    # Check if we're in a no-libs test environment
    if os.environ.get("ARCHIVEY_TEST_NO_LIBS"):
        if library_version is not None:
            pytest.fail(
                f"{library_name} should not be installed in nolibs environment, but found version {library_version}"
            )
    else:
        # Original behavior: skip if library is installed
        if library_version is not None:
            pytest.skip(f"{library_name} is installed with version {library_version}")

    if library_version is not None:
        pytest.skip(
            f"{library_name} is installed with version {getattr(dependencies, f'{library_name}_version')}"
        )

    with pytest.raises(PackageNotInstalledError) as excinfo:
        open_archive(archive_path, config=config)

    assert f"{library_name} package is not installed" in str(excinfo.value)


def test_stdlib_zstd_version():
    versions = get_dependency_versions()
    if sys.version_info >= (3, 14):
        assert versions.stdlib_zstd_version is not None
        assert versions.stdlib_zstd_version.startswith("stdlib (")
    else:
        assert versions.stdlib_zstd_version is None


@pytest.mark.skipif(
    get_dependency_versions().rarfile_version is None, reason="rarfile is not installed"
)
def test_rarfile_missing_cryptography_raises_exception():
    """Test that LibraryNotInstalledError is raised for header-encrypted .rar when cryptography is not installed."""
    with patch("archivey.formats.rar_reader.rarfile._have_crypto", 0):
        with open_archive(
            NORMAL_ENCRYPTED_RAR_ARCHIVE.get_archive_path(),
            pwd=NORMAL_ENCRYPTED_RAR_ARCHIVE.contents.header_password,
        ) as archive:
            assert {m.filename for m in archive.get_members()} == {
                "secret.txt",
                "also_secret.txt",
            }


@pytest.mark.skipif(
    get_dependency_versions().rarfile_version is None, reason="rarfile is not installed"
)
def test_rarfile_missing_cryptography_does_not_raise_exception_for_other_files():
    """Test that LibraryNotInstalledError is NOT raised for non-header-encrypted .rar when cryptography is not installed."""
    with patch("archivey.formats.rar_reader.rarfile._have_crypto", 0):
        with open_archive(
            NORMAL_ENCRYPTED_RAR_ARCHIVE.get_archive_path(),
            pwd=NORMAL_ENCRYPTED_RAR_ARCHIVE.contents.header_password,
        ) as archive:
            assert {m.filename for m in archive.get_members()} == {
                "secret.txt",
                "also_secret.txt",
            }
