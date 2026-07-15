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
    assert any("conflict" in r.getMessage().lower() for r in caplog.records), (
        caplog.text
    )


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


def test_lzma_alone_detected_by_content_probe() -> None:
    import lzma

    data = lzma.compress(b"lzma alone payload " * 20, format=lzma.FORMAT_ALONE)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.LZMA_ALONE
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


def test_lzma_alone_probe_does_not_claim_lzip() -> None:
    from tests.streams_util import make_lzip_member

    info = detect_format(io.BytesIO(make_lzip_member(b"lzip payload")))
    assert info.format == ArchiveFormat.LZIP
    assert info.detected_by == "magic"


def test_lzma_alone_probe_does_not_steal_zlib() -> None:
    data = zlib.compress(b"zlib payload that must stay zlib")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.ZLIB


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


@requires("ncompress")
def test_inner_tar_over_unix_compress_is_tar_z() -> None:
    """Bounded peek reader is seekable within its limit for inner-TAR upgrade."""
    from archivey.types import ContainerFormat, StreamFormat
    from tests.streams_util import make_unix_compress

    data = make_unix_compress(_tar_bytes())
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNIX_COMPRESS)
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


@requires("ncompress")
def test_unix_compress_without_inner_tar_stays_bare_z() -> None:
    from tests.streams_util import make_unix_compress

    data = make_unix_compress(b"just some bytes, definitely not a tar header region")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.Z
    assert info.detected_by == "magic"


@pytest.mark.parametrize(
    "hex_blob",
    [
        # Atheris detect_format finds (2026-07-14..15): consecutive LZW CLEARs share a
        # decompressed_offset; must not raise AssertionError during the inner-TAR probe.
        "1f9d9d001ffd37250000000000000000000000001b001f9d9d061ffd377a00df0000000900",
        "1f9d9d28a600000000000000000040f8000020000000ffff00000000f0",
        "1f9d9e9d009e58000000002600e38623a800288027",
        "1f9d8d8d00000000000000000000000000000000000000000000000000000000e2000000000000008d0000000000000000000000000000",
        "1f9d8b008b0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002e01000000100000035501000000000000035500e00008",
    ],
    ids=["clear-a", "clear-b", "clear-c", "clear-d", "clear-e"],
)
def test_detect_format_atheris_z_clear_collisions_do_not_assert(hex_blob: str) -> None:
    """Atheris: hostile .Z with consecutive CLEARs must not crash detect_format."""
    data = bytes.fromhex(hex_blob)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.Z
    assert info.detected_by == "magic"


def test_inner_tar_over_lzma_alone_is_tar_lzma() -> None:
    import lzma

    from archivey.types import ContainerFormat, StreamFormat

    data = lzma.compress(_tar_bytes(), format=lzma.FORMAT_ALONE)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE)


def test_tlz_lzip_stays_tar_lzip(tmp_path: Path) -> None:
    from archivey.diagnostics import DiagnosticCode
    from archivey.types import ContainerFormat, StreamFormat
    from tests.streams_util import make_lzip_member

    path = tmp_path / "compat_lzip.tlz"
    path.write_bytes(make_lzip_member(_tar_bytes()))
    info = detect_format(path)
    assert info.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)
    assert DiagnosticCode.FORMAT_EXTENSION_CONFLICT not in info.diagnostics.counts


def test_tlz_alone_content_wins_with_extension_conflict(tmp_path: Path) -> None:
    import lzma

    from archivey.diagnostics import DiagnosticCode
    from archivey.types import ContainerFormat, StreamFormat

    path = tmp_path / "compat_lzma.tlz"
    path.write_bytes(lzma.compress(_tar_bytes(), format=lzma.FORMAT_ALONE))
    info = detect_format(path)
    assert info.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE)
    assert DiagnosticCode.FORMAT_EXTENSION_CONFLICT in info.diagnostics.counts


def _large_block_tar_bz2() -> bytes:
    """A ``.tar.bz2`` whose first bzip2 block is far larger than the detection prefix.

    bzip2 is block-transform (BWT) based: it emits *no* decompressed output until a whole
    block (up to 900 KB) has been read. A first member of incompressible data makes the
    first block's *compressed* size exceed ``DETECTION_LIMIT`` (4096), so the header region
    (``ustar`` at offset 257) is unreachable from the peeked prefix alone.
    """
    import bz2
    import os
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = os.urandom(200_000)  # incompressible => first block compresses poorly
        info = tarfile.TarInfo("first.bin")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return bz2.compress(buf.getvalue(), 9)


def test_inner_tar_over_bzip2_large_block_is_tar_bz2() -> None:
    # Regression: a tar.bz2 whose first block exceeds the 4 KiB detection prefix must still
    # be recognized as TAR_BZ2 — the probe reads a full block from the source, not just the
    # prefix. Previously this decoded to zero bytes and was mis-reported as bare BZ2.
    info = detect_format(io.BytesIO(_large_block_tar_bz2()))
    assert info.format == ArchiveFormat.TAR_BZ2
    assert info.detected_by == "content_probe"


def test_inner_tar_over_bzip2_large_block_non_seekable() -> None:
    # Same, from a non-seekable pipe wrapped as the opener does: the PeekableStream buffers
    # enough of the prefix for the probe to reach the header region, and the source is not
    # consumed (the backend can still read the whole archive afterwards).
    from archivey.internal.streams.peekable import PeekableStream

    data = _large_block_tar_bz2()
    stream = PeekableStream(NonSeekableBytesIO(data))
    info = detect_format(stream)
    assert info.format == ArchiveFormat.TAR_BZ2
    assert stream.read(len(data)) == data


def test_bare_bzip2_large_block_stays_bare_bz2() -> None:
    # A large-block bare .bz2 that is NOT a tar must not be mis-promoted: the probe reads a
    # full block, finds no ustar, and reports bare BZ2 (bounded read, no false positive).
    import bz2
    import os

    data = bz2.compress(b"not a tar; " + os.urandom(200_000), 9)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.BZ2
    assert info.detected_by == "magic"


def test_inner_tar_probe_skipped_when_codec_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the zstd backend absent, a .tar.zst can't be probed: per the spec, detection
    # reports the *bare* compressor (ZST, by its magic) and defers the inner-TAR
    # determination to open time — without warning about the benign tar.zst/zst mismatch.
    monkeypatch.setattr(codecs_module, "_zstd", None)
    path = tmp_path / "thing.tar.zst"
    path.write_bytes(
        b"\x28\xb5\x2f\xfd" + b"\x00" * 64
    )  # zstd magic, unprobeable payload
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


# ---------------------------------------------------------------------------
# Stream-position contract: detection reads from and restores the current position
# ---------------------------------------------------------------------------


def test_detection_from_mid_positioned_stream() -> None:
    # The archive starts wherever the caller positioned the stream: detection must peek
    # from there (an embedded archive after junk bytes) and restore the position.
    junk = b"JUNKJUNK" * 16
    stream = io.BytesIO(junk + _zip_bytes())
    stream.seek(len(junk))
    info = detect_format(stream)
    assert info.format == ArchiveFormat.ZIP
    assert stream.tell() == len(junk)  # starting position restored, not rewound to 0
