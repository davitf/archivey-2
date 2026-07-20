"""Corrupt/truncated-input handling for the random-access accelerators.

`test_codecs.py` only exercises the *stdlib* decompressor paths; these cover the optional
`rapidgzip` accelerator, which backs both gzip (`RapidgzipFile`) and bzip2 (its bundled
`IndexedBzip2File`) and whose exception taxonomy differs from the stdlib decoders'. The
accelerators are forced ON (and skipped when `rapidgzip` is absent). Truncation: rapidgzip
often soft-EOFs by design; Archivey backstops with empty→stdlib fallback and a single-member
ISIZE compare on path sources (see OpenSpec `rapidgzip-truncation-investigation`).
"""

from __future__ import annotations

import bz2
import gzip
import io
from pathlib import Path

import pytest

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.config import (
    AcceleratorMode,
    StreamConfig,
)
from archivey.internal.streams.codecs import Codec, open_codec_stream

_GZ_ON = StreamConfig(use_rapidgzip=AcceleratorMode.ON)
_BZ_ON = StreamConfig(use_indexed_bzip2=AcceleratorMode.ON)


def _write(tmp_path: Path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# --- rapidgzip -------------------------------------------------------------------------


def test_rapidgzip_corrupt_translates_to_corruption() -> None:
    pytest.importorskip("rapidgzip")
    corrupt = bytearray(gzip.compress(b"payload " * 200))
    corrupt[15:35] = b"\x00" * 20  # clobber the deflate body past the 10-byte header
    # An in-memory source exercises the "Failed to parse gzip/zlib header" path that was
    # previously left untranslated (leaking a raw RuntimeError).
    with open_codec_stream(Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_GZ_ON) as s:
        with pytest.raises(CorruptionError):
            s.read()


@pytest.mark.parametrize(
    "message",
    [
        # Classic non-ISA-L (macOS) body-corruption wording.
        (
            "Failed to decode deflate block at 10 B 0 b because of: "
            "The backreferenced distance lies outside the window buffer!"
        ),
        # Observed on macOS CI for a clobbered zlib body (Huffman tables invalid):
        # "Failed to read deflate block header … The Huffman coding is not optimal!"
        (
            "Failed to read deflate block header at offset 2 B 0 b "
            "(position after trying: 10 B 7 b: The Huffman coding is not optimal!"
        ),
        # Bare Huffman phrasing — keep covered even if the prefix changes.
        "The Huffman coding is not optimal!",
    ],
    ids=["decode-block", "read-block-header-huffman", "huffman-only"],
)
def test_rapidgzip_macos_deflate_corruption_messages_are_translated(
    message: str,
) -> None:
    # Non-ISA-L rapidgzip (macOS) reports corrupt deflate as ValueError with several
    # message shapes; Linux ISA-L uses RuntimeError/IsalInflateWrapper instead. Assert
    # every known shape maps to CorruptionError so a raw ValueError never leaks —
    # platform-independently, without needing the macOS backend installed.
    from archivey.internal.streams.codecs import DeflateCodec, GzipCodec, ZlibCodec

    exc = ValueError(message)
    assert isinstance(GzipCodec()._translate_accelerator(exc), CorruptionError)
    assert isinstance(DeflateCodec()._translate_accelerator(exc), CorruptionError)
    assert isinstance(ZlibCodec()._translate_accelerator(exc), CorruptionError)


def test_rapidgzip_truncation_is_reported(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    full = gzip.compress(b"the quick brown fox " * 5000)
    path = _write(tmp_path, "truncated.gz", full[: len(full) // 2])
    # Truncation must surface as a read/close error (testing-contract: "CorruptionError or
    # TruncatedError"). Linux often soft-EOFs then empty→stdlib / ISIZE → TruncatedError;
    # macOS often raises from rapidgzip itself (CorruptionError). Either satisfies.
    with pytest.raises((TruncatedError, CorruptionError)):
        with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
            s.read()


def test_rapidgzip_header_only_truncation_raises(tmp_path: Path) -> None:
    """Bare 10-byte gzip header: rapidgzip silent-empty; empty→stdlib must raise."""
    pytest.importorskip("rapidgzip")
    path = _write(tmp_path, "header.gz", bytes.fromhex("1f8b08000000000000ff"))
    with pytest.raises((TruncatedError, CorruptionError)):
        with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
            s.read()


def test_rapidgzip_empty_payload_still_ok(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    path = _write(tmp_path, "empty.gz", gzip.compress(b""))
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        assert s.read() == b""


def test_rapidgzip_silent_empty_fallback_recovers_prefix(tmp_path: Path) -> None:
    """When rapidgzip returns empty, stdlib fallback streams a correct prefix then errors."""
    pytest.importorskip("rapidgzip")
    payload = b"the quick brown fox jumps over the lazy dog.\n" * 800
    full = gzip.compress(payload)
    # Mid-body cut: Linux rapidgzip typically silent-empty; macOS may raise instead.
    path = _write(tmp_path, "mid.gz", full[: max(18, len(full) // 2)])
    recovered = b""
    with pytest.raises((TruncatedError, CorruptionError)):
        with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
            recovered = s.read()
            s.read()
    if recovered:
        assert recovered == payload[: len(recovered)]
        assert len(recovered) < len(payload)


def test_rapidgzip_intact_single_member_reads_clean(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    payload = b"the quick brown fox " * 5000
    path = _write(tmp_path, "ok.gz", gzip.compress(payload))
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        assert s.read() == payload


def test_rapidgzip_multimember_not_flagged(tmp_path: Path) -> None:
    # The ISIZE backstop must not false-flag a valid concatenated gzip (its trailer is only
    # the last member's size). Multi-member ISIZE summing is deferred — further-magic bailout.
    pytest.importorskip("rapidgzip")
    data = gzip.compress(b"A" * 4000) + gzip.compress(b"B" * 2500)
    path = _write(tmp_path, "multi.gz", data)
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        assert s.read() == b"A" * 4000 + b"B" * 2500


# --- bzip2 (via rapidgzip's bundled IndexedBzip2File) ----------------------------------


def test_indexed_bzip2_corrupt_translates_to_corruption(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    corrupt = bytearray(bz2.compress(b"payload " * 400))
    corrupt[20:45] = b"\x00" * 25  # clobber block data/header
    path = _write(tmp_path, "corrupt.bz2", bytes(corrupt))
    with open_codec_stream(Codec.BZIP2, path, config=_BZ_ON) as s:
        with pytest.raises(CorruptionError):
            s.read()


def test_indexed_bzip2_intact_reads_clean(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    payload = b"payload " * 400
    path = _write(tmp_path, "ok.bz2", bz2.compress(payload))
    with open_codec_stream(Codec.BZIP2, path, config=_BZ_ON) as s:
        assert s.read() == payload
