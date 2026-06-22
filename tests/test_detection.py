"""Format-detection tests — Stage 1 scope (magic, extension, conflict, never-consumes).

Brotli/inner-TAR/ISO probes and SFX scanning land with their backends in later stages.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pytest

from archivey import ArchiveFormat, DetectionConfidence, FormatInfo, detect_format
from archivey.internal.errors import FormatDetectionError
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
    from archivey.internal.reader import ReadBackend
    from archivey.internal.registry import BackendRegistry

    class _MagicBackend(ReadBackend):
        FORMATS = (ArchiveFormat.SEVEN_Z,)
        MAGIC = ((0, b"\x37\x7a\xbc\xaf\x27\x1c", ArchiveFormat.SEVEN_Z),)

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
