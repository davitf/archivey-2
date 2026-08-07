"""Backend-registry tests — Stage 1 scope.

Covers always-register-via-sentinel, tri-state FULL/PARTIAL/NONE availability,
``list_supported_formats()`` vs ``list_known_formats()``, install-hint selection errors,
and two simultaneous readers. The NONE-end-to-end ISO-without-pycdlib path is exercised in
Stage 4 with the real ISO backend.
"""

from __future__ import annotations

import importlib.util
import io
import operator
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    FormatSupport,
    StreamCapability,
    StreamNotSeekableError,
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
from tests.sample_archives import (
    CORPUS,
    FORMAT_KEYS,
    corpus_archive_path,
    skip_unless_runnable,
)

# ---------------------------------------------------------------------------
# Synthetic backends, registered into a *fresh* registry (no global pollution)
# ---------------------------------------------------------------------------


class _CoreBackend(ReadBackend):
    FORMATS = (ArchiveFormat.TAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".tar": ArchiveFormat.TAR}
    MAGIC = (MagicSignature(257, b"ustar", ArchiveFormat.TAR),)

    def open_read(
        self, source, streaming, password, encoding, archive_name
    ):  # pragma: no cover
        raise NotImplementedError


class _OptionalPresentBackend(ReadBackend):
    FORMATS = (ArchiveFormat.RAR,)
    OPTIONAL_DEPENDENCY = "io"  # a module that always imports
    INSTALL_HINT = "pip install archivey[present]"

    def open_read(
        self, source, streaming, password, encoding, archive_name
    ):  # pragma: no cover
        raise NotImplementedError


class _OptionalMissingBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    OPTIONAL_DEPENDENCY = "a_package_that_does_not_exist_xyz"
    INSTALL_HINT = "pip install archivey[recommended]"

    def open_read(
        self, source, streaming, password, encoding, archive_name
    ):  # pragma: no cover
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


def test_optional_missing_backend_is_known_but_unavailable(
    registry: BackendRegistry,
) -> None:
    # Registered regardless of its dependency: present in "known", absent from "supported".
    assert ArchiveFormat.ISO in registry.list_known_formats()
    assert ArchiveFormat.ISO not in registry.list_supported_formats()

    avail = registry.format_availability(ArchiveFormat.ISO)
    assert avail.support is FormatSupport.NONE
    assert len(avail.missing) == 1
    assert avail.missing[0].name == "a_package_that_does_not_exist_xyz"
    assert "archivey[recommended]" in avail.missing[0].install_hint


def test_optional_present_backend_is_full(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.RAR)
    assert avail.support is FormatSupport.FULL
    assert avail.missing == ()
    assert ArchiveFormat.RAR in registry.list_supported_formats()


def test_core_backend_is_full(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.TAR)
    assert avail.support is FormatSupport.FULL


def test_unknown_format_is_none(registry: BackendRegistry) -> None:
    avail = registry.format_availability(ArchiveFormat.SEVEN_Z)
    assert avail.support is FormatSupport.NONE
    assert avail.missing == ()


def test_reader_for_missing_dependency_raises_with_hint(
    registry: BackendRegistry,
) -> None:
    with pytest.raises(UnsupportedFormatError) as excinfo:
        registry.reader_for_format(ArchiveFormat.ISO)
    msg = str(excinfo.value)
    assert "a_package_that_does_not_exist_xyz" in msg
    assert "archivey[recommended]" in msg


def test_reader_for_unknown_format_raises(registry: BackendRegistry) -> None:
    with pytest.raises(UnsupportedFormatError):
        registry.reader_for_format(ArchiveFormat.SEVEN_Z)


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
    # ZIP can store deflate64 (inflate64) and zstd, both in [recommended]; with those absent it
    # still opens and lists common (stored/deflate) members -> PARTIAL.
    monkeypatch.setattr(codecs_module, "_inflate64", None)
    monkeypatch.setattr(codecs_module, "_zstd", None)

    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.PARTIAL
    missing_names = {m.name for m in avail.missing}
    assert {"inflate64", "backports.zstd"} <= missing_names
    # PARTIAL formats are still "supported" (readable for their common members).
    assert ArchiveFormat.ZIP in list_supported_formats()


def test_zip_full_when_optional_codecs_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the codec layer wired into ZIP member reads, ZIP reports FULL when every
    # optional member codec backend is installed (deflate64 / zstd / ppmd).
    for sentinel in ("_inflate64", "_zstd", "_pyppmd"):
        monkeypatch.setattr(codecs_module, sentinel, object())
    avail = format_availability(ArchiveFormat.ZIP)
    assert avail.support is FormatSupport.FULL
    assert avail.missing == ()
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
    assert any("archivey[recommended]" in m.install_hint for m in avail.missing)

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


@pytest.mark.skipif(
    importlib.util.find_spec("pycdlib") is None,
    reason=(
        "with pycdlib genuinely absent the registry gate rejects ISO before dispatch "
        "reaches the backend (that path is test_iso_none_without_pycdlib); this test "
        "needs the gate to pass so the reader's own raise runs"
    ),
)
def test_iso_reader_missing_pycdlib_hint_matches_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader's own raise must advise the same extra the registry reports.

    Patching only ``iso_reader.pycdlib`` (not the registry's ``_optional``) lets dispatch
    reach the backend, which is the path that used to name a deleted ``[iso]`` extra while
    ``format_availability`` already said ``[recommended]``.
    """
    import io

    from archivey.exceptions import PackageNotInstalledError
    from archivey.internal.backends import iso_reader

    monkeypatch.setattr(iso_reader, "pycdlib", None)
    with pytest.raises(PackageNotInstalledError) as excinfo:
        open_archive(io.BytesIO(b"not an iso"), format=ArchiveFormat.ISO)
    message = str(excinfo.value)
    assert "pycdlib" in message
    assert "archivey[recommended]" in message
    assert iso_reader.IsoReadBackend.INSTALL_HINT in message


# ---------------------------------------------------------------------------
# required_source — the pipe/seek split, as data
# ---------------------------------------------------------------------------


class _NonSeekable(io.RawIOBase):
    """Forward-only byte source — the pipe shape."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False


# The documented table, written out rather than derived, so a backend that flips
# SUPPORTS_STREAMING_NON_SEEKABLE has to change this list too instead of silently
# agreeing with itself.
_FORWARD_ONLY_CONTAINERS = {ContainerFormat.TAR, ContainerFormat.RAW_STREAM}


@pytest.mark.parametrize("fmt", list_known_formats(), ids=lambda f: f.display_name)
def test_required_source_is_declared_for_every_known_format(fmt: ArchiveFormat) -> None:
    """Every known format answers "can I read this from a pipe?" without being tried."""
    expected = (
        StreamCapability.FORWARD_ONLY
        if fmt.container in _FORWARD_ONLY_CONTAINERS
        else StreamCapability.SEEKABLE
    )
    assert format_availability(fmt).required_source is expected


@pytest.mark.parametrize("key", ["tar", "tar.gz", "gz", "zip", "iso", "7z"])
def test_required_source_predicts_whether_a_pipe_works(
    key: str, tmp_path: Path
) -> None:
    """The field is only worth having if it agrees with what actually happens.

    Opening from a non-seekable source succeeds exactly when the format's
    ``required_source`` is satisfied by ``FORWARD_ONLY`` — which is what makes the
    comparison against ``reader.cost.stream_capability`` a usable substitute for
    catching ``StreamNotSeekableError``.
    """
    entry = next(
        e for e in CORPUS if e.id == ("single-file" if key == "gz" else "basic")
    )
    skip_unless_runnable(entry, key)
    data = corpus_archive_path(entry, key, tmp_path).read_bytes()

    required = format_availability(FORMAT_KEYS[key]).required_source
    pipe_is_enough = required <= StreamCapability.FORWARD_ONLY

    if pipe_is_enough:
        with open_archive(_NonSeekable(data), streaming=True) as reader:
            assert reader.cost.stream_capability is StreamCapability.FORWARD_ONLY
    else:
        with pytest.raises(StreamNotSeekableError):
            open_archive(_NonSeekable(data), streaming=True)


def test_required_source_survives_a_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whether ISO needs to seek does not depend on whether ``pycdlib`` is installed."""
    monkeypatch.setattr(
        "archivey.internal.registry._optional", lambda name: None, raising=True
    )
    availability = format_availability(ArchiveFormat.ISO)
    assert availability.support is FormatSupport.NONE
    assert availability.required_source is StreamCapability.SEEKABLE


def test_unregistered_format_defaults_to_the_conservative_answer() -> None:
    """A format nothing can read has no honest source shape; SEEKABLE sends you to buffer."""
    availability = BackendRegistry().format_availability(ArchiveFormat.ZIP)
    assert availability.support is FormatSupport.NONE
    assert availability.required_source is StreamCapability.SEEKABLE


def test_stream_capability_is_ordered_weakest_first() -> None:
    """``required_source`` reads as a minimum only because the enum is ordered."""
    assert StreamCapability.FORWARD_ONLY < StreamCapability.SEEKABLE
    assert StreamCapability.SEEKABLE >= StreamCapability.FORWARD_ONLY
    assert StreamCapability.FORWARD_ONLY <= StreamCapability.FORWARD_ONLY
    assert not (StreamCapability.SEEKABLE < StreamCapability.FORWARD_ONLY)
    assert sorted(StreamCapability) == [
        StreamCapability.FORWARD_ONLY,
        StreamCapability.SEEKABLE,
    ]


def test_stream_capability_does_not_compare_against_foreign_types() -> None:
    with pytest.raises(TypeError):
        operator.lt(StreamCapability.SEEKABLE, "seekable")
