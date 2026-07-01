"""Format-detection tests — Stage 1 + Stage 2 (Brotli content probe, weak zlib).

Inner-TAR / ISO probes and SFX scanning land with their backends in later stages.
"""

from __future__ import annotations

import io
import logging
import zipfile
import zlib
from pathlib import Path

import pytest

from archivey import ArchiveFormat, DetectionConfidence, FormatInfo, detect_format
from archivey.exceptions import FormatDetectionError
from archivey.internal.streams import codecs as codecs_module
from archivey.types import MagicSignature
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"hello")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Magic-byte detection
# ---------------------------------------------------------------------------


def test_magic_match_is_certain() -> None:
    info = detect_format(io.BytesIO(_zip_bytes()))
    assert info == FormatInfo(
        ArchiveFormat.ZIP, DetectionConfidence.CERTAIN, "magic", None, 0
    )


def test_zip_empty_archive_magic() -> None:
    # An empty ZIP is just the end-of-central-directory record (PK\x05\x06).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    info = detect_format(io.BytesIO(buf.getvalue()))
    assert info.format == ArchiveFormat.ZIP
    assert info.detected_by == "magic"


def test_small_archive_still_detected() -> None:
    # A tiny ZIP (far smaller than any large probe window) is still detected by magic.
    data = _zip_bytes()
    assert len(data) < 4096
    assert detect_format(io.BytesIO(data)).format == ArchiveFormat.ZIP


# ---------------------------------------------------------------------------
# Extension fallback
# ---------------------------------------------------------------------------


def test_extension_only_is_guess(tmp_path: Path) -> None:
    # No magic match, but a .zip extension -> a GUESS by extension.
    path = tmp_path / "mystery.zip"
    path.write_bytes(b"not really a zip but ends in .zip")
    info = detect_format(path)
    assert info.format == ArchiveFormat.ZIP
    assert info.confidence == DetectionConfidence.GUESS
    assert info.detected_by == "extension"


def test_unrecognized_bytes_no_name_raises() -> None:
    with pytest.raises(FormatDetectionError):
        detect_format(io.BytesIO(b"this is not any known archive format at all"))


def test_unrecognized_extension_and_bytes_raises(tmp_path: Path) -> None:
    path = tmp_path / "data.unknownext"
    path.write_bytes(b"random bytes")
    with pytest.raises(FormatDetectionError):
        detect_format(path)


# ---------------------------------------------------------------------------
# Conflict resolution: magic wins, a warning is emitted
# ---------------------------------------------------------------------------


def test_magic_wins_over_conflicting_extension(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A conflict needs two formats registered (only ZIP is, in Stage 1), so drive it
    # through a registry with two synthetic backends: magic says SEVEN_Z, the ".rar"
    # extension says RAR. Magic must win, with a WARNING on archivey.detection.
    from archivey.internal import detection as detection_module
    from archivey.internal.base_reader import ReadBackend
    from archivey.internal.registry import BackendRegistry

    class _MagicBackend(ReadBackend):
        FORMATS = (ArchiveFormat.SEVEN_Z,)
        MAGIC = (MagicSignature(0, b"\x37\x7a\xbc\xaf\x27\x1c", ArchiveFormat.SEVEN_Z),)

        def open_read(self, *a, **k):  # pragma: no cover
            raise NotImplementedError

    class _ExtBackend(ReadBackend):
        FORMATS = (ArchiveFormat.RAR,)
        EXTENSIONS = {".rar": ArchiveFormat.RAR}

        def open_read(self, *a, **k):  # pragma: no cover
            raise NotImplementedError

    reg = BackendRegistry()
    reg.register_reader(_MagicBackend)
    reg.register_reader(_ExtBackend)
    monkeypatch.setattr(detection_module, "get_registry", lambda: reg)

    path = tmp_path / "thing.rar"
    path.write_bytes(b"\x37\x7a\xbc\xaf\x27\x1c" + b"\x00" * 32)
    with caplog.at_level(logging.WARNING, logger="archivey.detection"):
        info = detect_format(path)
    assert info.format == ArchiveFormat.SEVEN_Z
    assert info.detected_by == "magic"
    assert any("conflict" in r.getMessage().lower() for r in caplog.records), caplog.text


def test_no_warning_when_extension_agrees(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(_zip_bytes())
    with caplog.at_level(logging.WARNING, logger="archivey.detection"):
        detect_format(path)
    assert not caplog.records


# ---------------------------------------------------------------------------
# Detection never consumes bytes
# ---------------------------------------------------------------------------


def test_seekable_stream_rewound_to_zero() -> None:
    stream = io.BytesIO(_zip_bytes())
    detect_format(stream)
    assert stream.tell() == 0
    # The full stream is still readable from the start.
    assert stream.read(4) == b"\x50\x4b\x03\x04"


def test_peekable_stream_not_consumed() -> None:
    from archivey.internal.streams.peekable import PeekableStream

    data = _zip_bytes()
    stream = PeekableStream(NonSeekableBytesIO(data))
    info = detect_format(stream)
    assert info.format == ArchiveFormat.ZIP
    # Nothing consumed: the backend can still read the whole archive.
    assert stream.read(len(data)) == data


def test_path_source_not_left_open(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_zip_bytes())
    # Detecting a path opens and closes its own handle; the file stays usable afterwards.
    detect_format(path)
    assert path.read_bytes()[:4] == b"\x50\x4b\x03\x04"


# ---------------------------------------------------------------------------
# Stage 2: Brotli content probe (magic-less) + weak zlib
# ---------------------------------------------------------------------------


@requires("brotli")
def test_brotli_detected_by_content_probe() -> None:
    import brotli

    data = brotli.compress(b"some brotli payload to decode")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


def test_brotli_probe_skipped_when_backend_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the Brotli backend absent, the probe is skipped and detection falls back to the
    # .br extension guess rather than failing.
    monkeypatch.setattr(codecs_module, "_brotli", None)
    path = tmp_path / "thing.br"
    path.write_bytes(b"not a brotli stream, just bytes")
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.GUESS
    assert info.detected_by == "extension"


def test_zlib_weak_magic_confirmed_by_content_probe() -> None:
    data = zlib.compress(b"zlib payload")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.ZLIB
    # The weak 2-byte header is confirmed by a content probe -> PROBABLE / content_probe.
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


def test_zlib_probe_wins_over_misleading_extension(tmp_path: Path) -> None:
    # A genuine zlib stream named .xz: the content probe confirms zlib, so the (wrong)
    # extension does not override it.
    path = tmp_path / "thing.xz"
    path.write_bytes(zlib.compress(b"payload"))
    info = detect_format(path)
    assert info.format == ArchiveFormat.ZLIB
    assert info.detected_by == "content_probe"


def test_weak_zlib_magic_without_valid_stream_falls_through(tmp_path: Path) -> None:
    # A 0x78 0x9c prefix on non-zlib data: the weak magic matches but the content probe
    # fails, so detection falls through to the extension guess instead of claiming zlib.
    path = tmp_path / "thing.xz"
    path.write_bytes(b"\x78\x9c" + b"\xff" * 200)  # zlib header byte, then garbage
    info = detect_format(path)
    assert info.format == ArchiveFormat.XZ
    assert info.detected_by == "extension"


# ---------------------------------------------------------------------------
# Stage 3: inner-TAR probe over a single-file compressor
# ---------------------------------------------------------------------------


def _tar_bytes() -> bytes:
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 5
        t.addfile(info, io.BytesIO(b"hello"))
    return buf.getvalue()


def test_inner_tar_over_gzip_is_tar_gz() -> None:
    import gzip

    data = gzip.compress(_tar_bytes())
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.TAR_GZ
    # The inner-tar test is structural, weaker than an exact magic.
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


def test_gzip_without_inner_tar_stays_bare_gz() -> None:
    import gzip

    data = gzip.compress(b"just some bytes, definitely not a tar header region")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.GZ
    assert info.detected_by == "magic"


def test_inner_tar_over_xz_is_tar_xz() -> None:
    import lzma

    data = lzma.compress(_tar_bytes(), format=lzma.FORMAT_XZ)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.TAR_XZ


def test_inner_tar_probe_skipped_when_codec_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the zstd backend absent, a .tar.zst can't be probed: per the spec, detection
    # reports the *bare* compressor (ZST, by its magic) and defers the inner-TAR
    # determination to open time — without warning about the benign tar.zst/zst mismatch.
    monkeypatch.setattr(codecs_module, "_zstd", None)
    path = tmp_path / "thing.tar.zst"
    path.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 64)  # zstd magic, unprobeable payload
    info = detect_format(path)
    assert info.format == ArchiveFormat.ZST
    assert info.detected_by == "magic"


def test_deferred_inner_tar_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A .tar.gz whose payload is NOT a tar: magic says bare GZ, extension says TAR_GZ. That
    # benign (same-stream) mismatch must not emit a conflict warning.
    import gzip

    path = tmp_path / "thing.tar.gz"
    path.write_bytes(gzip.compress(b"not a tar at all"))
    with caplog.at_level(logging.WARNING, logger="archivey.detection"):
        info = detect_format(path)
    assert info.format == ArchiveFormat.GZ
    assert not caplog.records


# ---------------------------------------------------------------------------
# Stage 4: ISO extended-peek window (CD001 at offset 32 769)
# ---------------------------------------------------------------------------


@requires("pycdlib")
def test_iso_detected_via_extended_window() -> None:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)
    iso.add_fp(io.BytesIO(b"x"), 1, "/X.TXT;1")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    info = detect_format(io.BytesIO(out.getvalue()))
    assert info.format == ArchiveFormat.ISO
    assert info.confidence == DetectionConfidence.CERTAIN
    assert info.detected_by == "magic"


def test_stream_too_short_for_iso_falls_through() -> None:
    # Far shorter than the 32 774-byte ISO window, and not any other format: ruled out as
    # ISO and raises FormatDetectionError (never rejected *solely* for being too short).
    with pytest.raises(FormatDetectionError):
        detect_format(io.BytesIO(b"tiny non-archive payload"))


def test_small_zip_still_detected_despite_iso_probe() -> None:
    # A 2 KiB-ish ZIP is matched by its offset-0 magic without ever taking the ISO window.
    data = _zip_bytes()
    assert detect_format(io.BytesIO(data)).format == ArchiveFormat.ZIP
