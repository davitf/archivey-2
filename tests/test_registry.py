"""Backend-registry tests — Stage 1 scope.

Covers always-register-via-sentinel, tri-state FULL/PARTIAL/NONE availability,
``list_supported_formats()`` vs ``list_known_formats()``, install-hint selection errors,
and two simultaneous readers. The NONE-end-to-end ISO-without-pycdlib path is exercised in
Stage 4 with the real ISO backend.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Mapping

import pytest

from archivey import (
    ArchiveFormat,
    FormatSupport,
    format_availability,
    list_known_formats,
    list_supported_formats,
    open_archive,
)
from archivey.internal.errors import UnsupportedFormatError
from archivey.internal.reader import ReadBackend
from archivey.internal.registry import BackendRegistry
from archivey.internal.streams import codecs as codecs_module
from archivey.internal.types import ContainerFormat

# ---------------------------------------------------------------------------
# Synthetic backends, registered into a *fresh* registry (no global pollution)
# ---------------------------------------------------------------------------


class _CoreBackend(ReadBackend):
    FORMATS = (ArchiveFormat.TAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".tar": ArchiveFormat.TAR}
    MAGIC = ((257, b"ustar", ArchiveFormat.TAR),)

    def open_read(self, source, streaming, password, encoding, archive_name):  # pragma: no cover
        raise NotImplementedError


class _OptionalPresentBackend(ReadBackend):
    FORMATS = (ArchiveFormat.SEVEN_Z,)
    OPTIONAL_DEPENDENCY = "io"  # a module that always imports
    INSTALL_HINT = "pip install archivey[present]"

    def open_read(self, source, streaming, password, encoding, archive_name):  # pragma: no cover
        raise NotImplementedError


class _OptionalMissingBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    OPTIONAL_DEPENDENCY = "a_package_that_does_not_exist_xyz"
    INSTALL_HINT = "pip install archivey[iso]"

    def open_read(self, source, streaming, password, encoding, archive_name):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register_reader(_CoreBackend)
    reg.register_reader(_OptionalPresentBackend)
    reg.register_reader(_OptionalMissingBackend)
    return reg


# ---------------------------------------------------------------------------
# Always-register; availability derived from the module-or-None sentinel
# ---------------------------------------------------------------------------


def test_optional_missing_backend_is_known_but_unavailable(registry: BackendRegistry) -> None:
    # Registered regardless of its dependency: present in "known", absent from "supported".
    assert ArchiveFormat.ISO in registry.list_known_formats()
    assert ArchiveFormat.ISO not in registry.list_supported_formats()

    avail = registry.format_availability(ArchiveFormat.ISO)
    assert avail.support is FormatSupport.NONE
    assert len(avail.missing) == 1
    assert avail.missing[0].name == "a_package_that_does_not_exist_xyz"
    assert "archivey[iso]" in avail.missing[0].install_hint


def test_optional_present_backend_is_full(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.SEVEN_Z)
    assert avail.support is FormatSupport.FULL
    assert avail.missing == ()
    assert ArchiveFormat.SEVEN_Z in registry.list_supported_formats()


def test_core_backend_is_full(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.TAR)
    assert avail.support is FormatSupport.FULL


def test_unknown_format_is_none(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.RAR)
    assert avail.support is FormatSupport.NONE
    assert avail.missing == ()


def test_reader_for_missing_dependency_raises_with_hint(registry: BackendRegistry) -> None:
    with pytest.raises(UnsupportedFormatError) as excinfo:
        registry.reader_for_format(ArchiveFormat.ISO)
    msg = str(excinfo.value)
    assert "a_package_that_does_not_exist_xyz" in msg
    assert "archivey[iso]" in msg


def test_reader_for_unknown_format_raises(registry: BackendRegistry) -> None:
    with pytest.raises(UnsupportedFormatError):
        registry.reader_for_format(ArchiveFormat.RAR)


# ---------------------------------------------------------------------------
# Detection tables aggregate backend data
# ---------------------------------------------------------------------------


def test_magic_and_extension_tables_aggregate(registry: BackendRegistry) -> None:
    assert (257, b"ustar", ArchiveFormat.TAR) in registry.magic_entries()
    assert registry.extension_map()[".tar"] == ArchiveFormat.TAR


# ---------------------------------------------------------------------------
# Tri-state PARTIAL: a multi-codec container missing an optional member codec
# ---------------------------------------------------------------------------


def test_zip_partial_when_optional_codecs_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ZIP can store deflate64 (inflate64 / [7z]) and zstd ([zstd]); with those absent it
    # still opens and lists common (stored/deflate) members -> PARTIAL.
    monkeypatch.setattr(codecs_module, "_inflate64", None)
    monkeypatch.setattr(codecs_module, "_zstandard", None)

    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.PARTIAL
    missing_names = {m.name for m in avail.missing}
    assert {"inflate64", "zstandard"} <= missing_names
    # PARTIAL formats are still "supported" (readable for their common members).
    assert ArchiveFormat.ZIP in list_supported_formats()


def test_zip_full_when_codecs_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force ZIP's optional member codecs present so FULL is asserted deterministically,
    # regardless of which extras the test environment installed (the core-only CI legs
    # have none). The PARTIAL direction is covered by
    # test_zip_partial_when_optional_codecs_missing.
    for sentinel in ("_inflate64", "_zstandard", "_pyppmd"):
        monkeypatch.setattr(codecs_module, sentinel, object())
    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.FULL
    assert avail.missing == ()


def test_single_codec_format_none_when_codec_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bare single-file compressor whose sole codec backend is missing is NONE (not
    # PARTIAL): there is nothing to fall back to. ZST's codec is zstandard.
    monkeypatch.setattr(codecs_module, "_zstandard", None)
    avail = format_availability(ArchiveFormat.ZST)
    assert avail.support is FormatSupport.NONE
    assert avail.missing[0].name == "zstandard"
    assert ArchiveFormat.ZST in list_known_formats()
    assert ArchiveFormat.ZST not in list_supported_formats()


def test_single_codec_format_full_when_codec_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codecs_module, "_zstandard", object())
    assert format_availability(ArchiveFormat.ZST).support is FormatSupport.FULL


# ---------------------------------------------------------------------------
# Global registry: known includes everything; supported excludes NONE
# ---------------------------------------------------------------------------


def test_global_known_and_supported() -> None:
    known = list_known_formats()
    supported = list_supported_formats()
    assert ArchiveFormat.ZIP in known
    assert ArchiveFormat.DIRECTORY in known
    assert ArchiveFormat.ZIP in supported
    # Supported is always a subset of known.
    assert set(supported) <= set(known)


def test_directory_is_full() -> None:
    assert format_availability(ArchiveFormat.DIRECTORY).support is FormatSupport.FULL


# ---------------------------------------------------------------------------
# Two simultaneous readers
# ---------------------------------------------------------------------------


def _make_zip(path: Path, content: bytes) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.txt", content)


def test_two_simultaneous_readers(tmp_path: Path) -> None:
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _make_zip(a, b"alpha")
    _make_zip(b, b"beta")

    with open_archive(a) as ra, open_archive(b) as rb:
        # Independent readers: interleaved reads do not interfere.
        assert ra.read("data.txt") == b"alpha"
        assert rb.read("data.txt") == b"beta"
        assert ra.read("data.txt") == b"alpha"


def test_partial_container_does_not_lower_for_unrelated_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Removing a ZIP-relevant codec must not affect a codec-less format like DIRECTORY.
    monkeypatch.setattr(codecs_module, "_zstandard", None)
    assert format_availability(ArchiveFormat.DIRECTORY).support is FormatSupport.FULL
    assert ContainerFormat.DIRECTORY == ArchiveFormat.DIRECTORY.container
