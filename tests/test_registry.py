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
from archivey.exceptions import UnsupportedFormatError
from archivey.internal.base_reader import ReadBackend
from archivey.internal.registry import BackendRegistry
from archivey.internal.streams import codecs as codecs_module
from archivey.types import ContainerFormat, MagicSignature

# ---------------------------------------------------------------------------
# Synthetic backends, registered into a *fresh* registry (no global pollution)
# ---------------------------------------------------------------------------


class _CoreBackend(ReadBackend):
    FORMATS = (ArchiveFormat.TAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".tar": ArchiveFormat.TAR}
    MAGIC = (MagicSignature(257, b"ustar", ArchiveFormat.TAR),)

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
    assert MagicSignature(257, b"ustar", ArchiveFormat.TAR) in registry.magic_entries()
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
    monkeypatch.setattr(codecs_module, "_zstd", None)

    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.PARTIAL
    missing_names = {m.name for m in avail.missing}
    assert {"inflate64", "backports.zstd"} <= missing_names
    # PARTIAL formats are still "supported" (readable for their common members).
    assert ArchiveFormat.ZIP in list_supported_formats()


def test_zip_partial_even_when_codecs_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolved 2026-07 decision (see the phase-5 proposal): ZIP member *data* still decodes
    # via stdlib zipfile, which cannot use the optional codecs even when installed, so ZIP
    # reports PARTIAL (never FULL) until Phase 7 wires the codec layer into ZIP reads.
    # `missing` is empty when every optional codec is present (the gap is implementation
    # stage, not a missing install). Force them present so this holds regardless of which
    # extras the test environment installed (the core-only CI legs have none).
    for sentinel in ("_inflate64", "_zstd", "_pyppmd"):
        monkeypatch.setattr(codecs_module, sentinel, object())
    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.PARTIAL
    assert avail.missing == ()
    # PARTIAL formats are still "supported" (readable for their common members).
    assert ArchiveFormat.ZIP in list_supported_formats()


def test_single_codec_format_none_when_codec_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bare single-file compressor whose sole codec backend is missing is NONE (not
    # PARTIAL): there is nothing to fall back to. ZST's codec is backports.zstd / stdlib zstd.
    monkeypatch.setattr(codecs_module, "_zstd", None)
    avail = format_availability(ArchiveFormat.ZST)
    assert avail.support is FormatSupport.NONE
    assert avail.missing[0].name == "backports.zstd"
    assert ArchiveFormat.ZST in list_known_formats()
    assert ArchiveFormat.ZST not in list_supported_formats()


def test_single_codec_format_full_when_codec_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codecs_module, "_zstd", object())
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
    monkeypatch.setattr(codecs_module, "_zstd", None)
    assert format_availability(ArchiveFormat.DIRECTORY).support is FormatSupport.FULL
    assert ContainerFormat.DIRECTORY == ArchiveFormat.DIRECTORY.container


# ---------------------------------------------------------------------------
# Stage 4: NONE end-to-end — the real ISO backend without pycdlib degrades
# gracefully (simulated absence, so this runs in the core-only leg too).
# ---------------------------------------------------------------------------


def test_iso_none_without_pycdlib(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    import archivey.internal.backends  # noqa: F401 - ensures the ISO backend is registered
    from archivey.internal import registry as registry_module

    real_optional = registry_module._optional
    monkeypatch.setattr(
        registry_module,
        "_optional",
        lambda name: None if name == "pycdlib" else real_optional(name),
    )

    avail = format_availability(ArchiveFormat.ISO)
    assert avail.support is FormatSupport.NONE
    assert any(m.name == "pycdlib" for m in avail.missing)
    assert any("archivey[iso]" in m.install_hint for m in avail.missing)

    # NONE is excluded from the supported list but still known.
    assert ArchiveFormat.ISO not in list_supported_formats()
    assert ArchiveFormat.ISO in list_known_formats()

    # Selecting it raises an install-hint error rather than crashing.
    with pytest.raises(UnsupportedFormatError) as excinfo:
        open_archive(io.BytesIO(b"not an iso"), format=ArchiveFormat.ISO)
    assert "pycdlib" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Compressed tar: the outer stream codec is a single-codec gate (NONE, not PARTIAL)
# ---------------------------------------------------------------------------


def test_compressed_tar_none_when_stream_codec_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    # A compressed tar cannot even be listed without its outer codec, so per the
    # single-codec rule tar.zst is NONE with the codec's install hint — not FULL.
    monkeypatch.setattr(codecs_module, "_zstd", None)
    avail = format_availability(ArchiveFormat.TAR_ZST)
    assert avail.support is FormatSupport.NONE
    assert avail.missing[0].name == "backports.zstd"
    assert ArchiveFormat.TAR_ZST in list_known_formats()
    assert ArchiveFormat.TAR_ZST not in list_supported_formats()

    # Selecting it raises an install-hint error rather than failing later at decode time.
    with pytest.raises(UnsupportedFormatError) as excinfo:
        open_archive(io.BytesIO(b"not a tar.zst"), format=ArchiveFormat.TAR_ZST)
    assert "backports.zstd" in str(excinfo.value)


def test_compressed_tar_full_when_stream_codec_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codecs_module, "_zstd", object())
    assert format_availability(ArchiveFormat.TAR_ZST).support is FormatSupport.FULL
