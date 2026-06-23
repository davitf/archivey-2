"""Corrupt/truncated-input handling for the random-access accelerators.

`test_codecs.py` only exercises the *stdlib* decompressor paths; these cover the
optional `rapidgzip` / `indexed_bzip2` accelerators, whose exception taxonomies differ.
The accelerators are forced ON (and skipped when their package is absent). Truncation is
the interesting case: rapidgzip surfaces some truncations as exceptions but silently
returns short/zero output for others, so a backstop in `_open_gzip` checks the gzip ISIZE
trailer on a full read (disambiguating concatenated multi-member gzip).
"""

from __future__ import annotations

import bz2
import gzip
import io
from pathlib import Path

import pytest

from archivey.internal.config import (
    _ACCELERATORS_UNSAFE_PLATFORM,
    AcceleratorMode,
    StreamConfig,
)
from archivey.internal.errors import CorruptionError, TruncatedError
from archivey.internal.streams.codecs import Codec, open_codec_stream

# Forcing an accelerator ON exercises it in-process. On macOS that crashes the whole test
# process at interpreter shutdown (a detached C++ worker thread; see test_accelerator_shutdown.py
# and docs/known-issues.md), so these in-process accelerator tests are skipped there. Linux
# and Windows still exercise them fully.
pytestmark = pytest.mark.skipif(
    _ACCELERATORS_UNSAFE_PLATFORM,
    reason="rapidgzip/indexed_bzip2 abort the process at shutdown on macOS (see test_accelerator_shutdown.py)",
)

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


def test_rapidgzip_truncation_is_reported(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    full = gzip.compress(b"the quick brown fox " * 5000)
    path = _write(tmp_path, "truncated.gz", full[: len(full) // 2])
    # Truncation must surface as a read error (testing-contract: "CorruptionError or
    # TruncatedError"). Which one is platform-dependent: on Linux rapidgzip returns
    # silently and the ISIZE backstop raises TruncatedError; on macOS rapidgzip itself
    # raises (CorruptionError). Either satisfies the contract.
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        with pytest.raises((TruncatedError, CorruptionError)):
            s.read()


def test_rapidgzip_intact_single_member_reads_clean(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    payload = b"the quick brown fox " * 5000
    path = _write(tmp_path, "ok.gz", gzip.compress(payload))
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        assert s.read() == payload


def test_rapidgzip_multimember_not_flagged(tmp_path: Path) -> None:
    # The ISIZE backstop must not false-flag a valid concatenated gzip (its trailer is only
    # the last member's size).
    pytest.importorskip("rapidgzip")
    data = gzip.compress(b"A" * 4000) + gzip.compress(b"B" * 2500)
    path = _write(tmp_path, "multi.gz", data)
    with open_codec_stream(Codec.GZIP, path, config=_GZ_ON) as s:
        assert s.read() == b"A" * 4000 + b"B" * 2500


# --- indexed_bzip2 ---------------------------------------------------------------------


def test_indexed_bzip2_corrupt_translates_to_corruption(tmp_path: Path) -> None:
    pytest.importorskip("indexed_bzip2")
    corrupt = bytearray(bz2.compress(b"payload " * 400))
    corrupt[20:45] = b"\x00" * 25  # clobber block data/header
    path = _write(tmp_path, "corrupt.bz2", bytes(corrupt))
    with open_codec_stream(Codec.BZIP2, path, config=_BZ_ON) as s:
        with pytest.raises(CorruptionError):
            s.read()


def test_indexed_bzip2_intact_reads_clean(tmp_path: Path) -> None:
    pytest.importorskip("indexed_bzip2")
    payload = b"payload " * 400
    path = _write(tmp_path, "ok.bz2", bz2.compress(payload))
    with open_codec_stream(Codec.BZIP2, path, config=_BZ_ON) as s:
        assert s.read() == payload
