"""Cross-library matrix: the stream helpers vs. every stream type a caller might supply.

The ``streams/binaryio.py`` helpers (``is_stream`` / ``is_seekable`` / ``ensure_binaryio`` /
``ensure_bufferedio`` / ``BinaryIOWrapper``) are core infrastructure: every backend feeds
them whatever stream the *source* produced. This module verifies they behave correctly
against the real objects those sources return — local files, every stdlib codec stream,
zip/tar member streams, archivey's own decompressor streams, network responses (the stdlib
``http.client.HTTPResponse`` and ``urllib3``), and bare/partial duck-typed objects — plus
the nested-archive case where one archive's member stream is itself the source for another
reader. (httpx has no file-like to test: its streaming API is iterator-based.)

Each case is a *factory* (streams are single-use), tagged with whether it is already an
``io.IOBase`` (so ``is_stream`` passes it through) and whether it is seekable.
"""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import io
import lzma
import mmap
import os
import tarfile
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

import pytest

from archivey.internal.streams.binaryio import (
    BinaryIOWrapper,
    ensure_binaryio,
    ensure_bufferedio,
    is_seekable,
    is_stream,
)
from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.decompress import ZlibDecompressorStream
from archivey.internal.streams.lzip import LzipDecompressorStream
from archivey.internal.streams.xz import XzDecompressorStream
from tests.streams_util import NonSeekableBytesIO, make_lzip_member

# Non-trivial, non-repetitive payload so chunked reads and seeks are meaningful.
CONTENT = bytes((i * 7 + 13) % 256 for i in range(5000))

HAVE_URLLIB3 = importlib.util.find_spec("urllib3") is not None
HAVE_FSSPEC = importlib.util.find_spec("fsspec") is not None


def _close(stream: object) -> None:
    """Close ``stream`` if it has a ``close`` (the bare duck-typed cases deliberately don't)."""
    closer = getattr(stream, "close", None)
    if callable(closer):
        closer()


# --- partial / bare duck-typed objects (the reason BinaryIOWrapper exists) --------------


class OnlyReadStream:
    """A bare object with only ``read()`` — not an io.IOBase."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._inner.read(size)


class ReadIntoStream(OnlyReadStream):
    """Partial file-like that also implements ``readinto`` but is not an io.IOBase."""

    def readinto(self, b) -> int:  # type: ignore[no-untyped-def]
        return self._inner.readinto(b)


class FakeS3StreamingBody:
    """Mimics ``botocore.response.StreamingBody`` (S3 ``get_object()["Body"]``).

    The real object wraps a urllib3 response: it has ``read(amt=None)``, ``tell()``,
    ``close()`` and ``iter_chunks()`` / ``iter_lines()``, but is **not** an io.IOBase and
    has no ``seek``/``seekable``/``readinto`` — i.e. a forward-only partial file-like that
    must be wrapped. Modelled faithfully here rather than depending on botocore.
    """

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)

    def read(self, amt: int | None = None) -> bytes:
        return self._inner.read(amt if amt is not None else -1)

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        self._inner.close()


# --- stream factories ------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    build: Callable[[Path], BinaryIO]
    seekable: bool
    passes_is_stream: bool  # True => is_stream() accepts it, so ensure_binaryio passes it through


def _buffered_file(tmp_path: Path) -> BinaryIO:
    p = tmp_path / "buffered.bin"
    p.write_bytes(CONTENT)
    return open(p, "rb")


def _raw_file(tmp_path: Path) -> BinaryIO:
    p = tmp_path / "raw.bin"
    p.write_bytes(CONTENT)
    return open(p, "rb", buffering=0)


def _bytesio(_tmp_path: Path) -> BinaryIO:
    return io.BytesIO(CONTENT)


def _gzip_member(_tmp_path: Path) -> BinaryIO:
    return gzip.open(io.BytesIO(gzip.compress(CONTENT)), "rb")


def _bz2_member(_tmp_path: Path) -> BinaryIO:
    return bz2.open(io.BytesIO(bz2.compress(CONTENT)), "rb")


def _lzma_member(_tmp_path: Path) -> BinaryIO:
    return lzma.open(io.BytesIO(lzma.compress(CONTENT)), "rb")


def _zip_member(stored: bool) -> Callable[[Path], BinaryIO]:
    def build(_tmp_path: Path) -> BinaryIO:
        buf = io.BytesIO()
        method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(buf, "w", method) as z:
            z.writestr("m.bin", CONTENT)
        buf.seek(0)
        return zipfile.ZipFile(buf).open("m.bin")

    return build


def _tar_member(compression: str) -> Callable[[Path], BinaryIO]:
    def build(_tmp_path: Path) -> BinaryIO:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=f"w:{compression}" if compression else "w") as t:
            info = tarfile.TarInfo("m.bin")
            info.size = len(CONTENT)
            t.addfile(info, io.BytesIO(CONTENT))
        buf.seek(0)
        member = tarfile.open(fileobj=buf, mode="r:*").extractfile("m.bin")
        assert member is not None
        return member

    return build


def _xz_decompressor(_tmp_path: Path) -> BinaryIO:
    return XzDecompressorStream(io.BytesIO(lzma.compress(CONTENT, format=lzma.FORMAT_XZ)))


def _lzip_decompressor(_tmp_path: Path) -> BinaryIO:
    return LzipDecompressorStream(io.BytesIO(make_lzip_member(CONTENT)))


def _zlib_decompressor(_tmp_path: Path) -> BinaryIO:
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = co.compress(CONTENT) + co.flush()
    return ZlibDecompressorStream(io.BytesIO(raw), wbits=-15)


def _codec_gzip(_tmp_path: Path) -> BinaryIO:
    return open_codec_stream(Codec.GZIP, io.BytesIO(gzip.compress(CONTENT)))


def _urllib3_response(_tmp_path: Path) -> BinaryIO:
    from urllib3.response import HTTPResponse

    return HTTPResponse(body=io.BytesIO(CONTENT), status=200, preload_content=False)


def _http_client_response(_tmp_path: Path) -> BinaryIO:
    """A stdlib ``http.client.HTTPResponse`` (what ``urllib.request.urlopen`` returns).

    Built over an in-memory fake socket so no network is needed. It is a non-seekable
    ``io.BufferedIOBase`` — the canonical dependency-free "network stream" input.
    (httpx is intentionally not covered: its streaming API is iterator-based
    — ``iter_bytes``/``iter_raw`` — and exposes no ``read(n)`` file object to pass here.)
    """
    import http.client

    class _FakeSocket:
        def __init__(self, raw: bytes) -> None:
            self._raw = io.BytesIO(raw)

        def makefile(self, *args: object, **kwargs: object) -> io.BytesIO:
            return self._raw

    raw = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(CONTENT), CONTENT)
    resp = http.client.HTTPResponse(_FakeSocket(raw))  # type: ignore[arg-type]  # duck-typed sock
    resp.begin()
    return resp


def _only_read(_tmp_path: Path) -> BinaryIO:
    return OnlyReadStream(CONTENT)  # type: ignore[return-value]


def _read_into(_tmp_path: Path) -> BinaryIO:
    return ReadIntoStream(CONTENT)  # type: ignore[return-value]


def _non_seekable_bytesio(_tmp_path: Path) -> BinaryIO:
    return NonSeekableBytesIO(CONTENT)


def _spooled_tempfile(_tmp_path: Path) -> BinaryIO:
    # Common for buffering uploads/downloads in memory then spilling to disk.
    f = tempfile.SpooledTemporaryFile(max_size=len(CONTENT) // 2)  # forced to spill
    f.write(CONTENT)
    f.seek(0)
    return f


def _named_tempfile(_tmp_path: Path) -> BinaryIO:
    # _TemporaryFileWrapper: fully duck-types BinaryIO but is NOT an io.IOBase, so this
    # exercises the duck-typed branch of is_stream() on a real-world object.
    f = tempfile.NamedTemporaryFile()
    f.write(CONTENT)
    f.seek(0)
    return f


def _os_pipe_reader(_tmp_path: Path) -> BinaryIO:
    # A real OS non-seekable stream (BufferedReader over a pipe fd). CONTENT fits the
    # pipe buffer, so the single write doesn't block before the write end is closed.
    r, w = os.pipe()
    os.write(w, CONTENT)
    os.close(w)
    return open(r, "rb")


def _mmap_source(_tmp_path: Path) -> BinaryIO:
    # An anonymous mmap: seekable in practice, but it has no seekable() method and is not
    # an io.IOBase, so it gets wrapped — and is_seekable() conservatively reports it
    # non-seekable (see the module note / the open question raised in review).
    mm = mmap.mmap(-1, len(CONTENT))
    mm.write(CONTENT)
    mm.seek(0)
    return mm  # type: ignore[return-value]


def _s3_streaming_body(_tmp_path: Path) -> BinaryIO:
    return FakeS3StreamingBody(CONTENT)  # type: ignore[return-value]


def _fsspec_memory(_tmp_path: Path) -> BinaryIO:
    import fsspec

    fs = fsspec.filesystem("memory")
    path = "/stream_inputs_case.bin"
    with fs.open(path, "wb") as f:
        f.write(CONTENT)
    return fs.open(path, "rb")


CASES: dict[str, Case] = {
    # --- already-conforming io.IOBase streams (passed through unwrapped) ---
    "buffered_file": Case(_buffered_file, seekable=True, passes_is_stream=True),
    "raw_fileio": Case(_raw_file, seekable=True, passes_is_stream=True),
    "bytesio": Case(_bytesio, seekable=True, passes_is_stream=True),
    "spooled_tempfile": Case(_spooled_tempfile, seekable=True, passes_is_stream=True),
    "gzip": Case(_gzip_member, seekable=True, passes_is_stream=True),
    "bz2": Case(_bz2_member, seekable=True, passes_is_stream=True),
    "lzma": Case(_lzma_member, seekable=True, passes_is_stream=True),
    "zip_stored": Case(_zip_member(stored=True), seekable=True, passes_is_stream=True),
    "zip_deflated": Case(_zip_member(stored=False), seekable=True, passes_is_stream=True),
    "tar_uncompressed": Case(_tar_member(""), seekable=True, passes_is_stream=True),
    "tar_gz": Case(_tar_member("gz"), seekable=True, passes_is_stream=True),
    "xz_decompressor": Case(_xz_decompressor, seekable=True, passes_is_stream=True),
    "lzip_decompressor": Case(_lzip_decompressor, seekable=True, passes_is_stream=True),
    "zlib_decompressor": Case(_zlib_decompressor, seekable=True, passes_is_stream=True),
    "codec_gzip": Case(_codec_gzip, seekable=True, passes_is_stream=True),
    # --- non-seekable io.IOBase streams (network / pipes) ---
    "urllib3_response": Case(_urllib3_response, seekable=False, passes_is_stream=True),
    "http_client_response": Case(_http_client_response, seekable=False, passes_is_stream=True),
    "os_pipe_reader": Case(_os_pipe_reader, seekable=False, passes_is_stream=True),
    "non_seekable_bytesio": Case(_non_seekable_bytesio, seekable=False, passes_is_stream=True),
    # --- fully duck-typed but NOT io.IOBase (accepted via the method-set check) ---
    "named_tempfile": Case(_named_tempfile, seekable=True, passes_is_stream=True),
    "fsspec_memory": Case(_fsspec_memory, seekable=True, passes_is_stream=True),
    # --- partial / bare objects that must be wrapped ---
    "only_read": Case(_only_read, seekable=False, passes_is_stream=False),
    "read_into": Case(_read_into, seekable=False, passes_is_stream=False),
    "s3_streaming_body": Case(_s3_streaming_body, seekable=False, passes_is_stream=False),
    # mmap is seekable in practice but exposes no seekable() method, so is_seekable()
    # conservatively reports False and it is wrapped (see module note).
    "mmap": Case(_mmap_source, seekable=False, passes_is_stream=False),
}

_OPTIONAL_DEP = {"urllib3_response": ("urllib3", HAVE_URLLIB3), "fsspec_memory": ("fsspec", HAVE_FSSPEC)}


def _params() -> list:
    params = []
    for cid in CASES:
        marks = []
        if cid in _OPTIONAL_DEP:
            pkg, available = _OPTIONAL_DEP[cid]
            if not available:
                marks.append(pytest.mark.skip(reason=f"{pkg} not installed"))
        params.append(pytest.param(cid, id=cid, marks=marks))
    return params


@pytest.fixture(params=_params())
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]


# --- the matrix ------------------------------------------------------------------------


def test_is_stream_classification(case: Case, tmp_path: Path) -> None:
    stream = case.build(tmp_path)
    try:
        assert is_stream(stream) is case.passes_is_stream
    finally:
        _close(stream)


def test_is_seekable_matches_capability(case: Case, tmp_path: Path) -> None:
    stream = case.build(tmp_path)
    try:
        assert is_seekable(stream) is case.seekable
    finally:
        _close(stream)


def test_ensure_binaryio_passthrough_or_wrap(case: Case, tmp_path: Path) -> None:
    stream = case.build(tmp_path)
    try:
        ensured = ensure_binaryio(stream)
        if case.passes_is_stream:
            assert ensured is stream  # already a BinaryIO; not re-wrapped
        else:
            assert isinstance(ensured, BinaryIOWrapper)  # partial object gets adapted
    finally:
        _close(stream)


def test_full_read_yields_content(case: Case, tmp_path: Path) -> None:
    stream = ensure_binaryio(case.build(tmp_path))
    try:
        assert stream.read() == CONTENT
    finally:
        _close(stream)


def test_chunked_read_yields_content(case: Case, tmp_path: Path) -> None:
    stream = ensure_binaryio(case.build(tmp_path))
    try:
        chunks = []
        while True:
            chunk = stream.read(64)
            if not chunk:
                break
            chunks.append(chunk)
        assert b"".join(chunks) == CONTENT
    finally:
        _close(stream)


def test_readinto_yields_content(case: Case, tmp_path: Path) -> None:
    stream = ensure_binaryio(case.build(tmp_path))
    try:
        out = bytearray()
        buf = bytearray(128)
        while True:
            n = stream.readinto(buf)
            if not n:
                break
            out.extend(buf[:n])
        assert bytes(out) == CONTENT
    finally:
        _close(stream)


def test_ensure_bufferedio_yields_content(case: Case, tmp_path: Path) -> None:
    stream = case.build(tmp_path)
    try:
        buffered = ensure_bufferedio(stream)
        assert buffered.read() == CONTENT
    finally:
        _close(stream)


def test_seek_rewind_when_seekable(case: Case, tmp_path: Path) -> None:
    """A seekable source can be rewound and re-read; a non-seekable one reports so."""
    stream = ensure_binaryio(case.build(tmp_path))
    try:
        if not case.seekable:
            assert not is_seekable(stream)
            return
        head = stream.read(100)
        assert head == CONTENT[:100]
        stream.seek(0)
        assert stream.read(100) == CONTENT[:100]
    finally:
        _close(stream)


# --- nested archives: a member stream is itself the source for another reader -----------


def test_nested_zip_member_as_codec_source() -> None:
    """A gzip stream stored as a ZIP member, then decompressed via the codec layer.

    The ZIP member stream (``ZipExtFile``) is the *source* handed to the codec layer — the
    nested-archive pattern, where what you read out of one container feeds another.
    """
    gz_bytes = gzip.compress(CONTENT)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("inner.gz", gz_bytes)
    buf.seek(0)
    with zipfile.ZipFile(buf) as z:
        member_stream = z.open("inner.gz")
        with open_codec_stream(Codec.GZIP, member_stream) as decompressed:
            assert decompressed.read() == CONTENT


def test_nested_tar_member_feeds_zipfile() -> None:
    """A whole ZIP archive stored inside a TAR, opened straight from the TAR member stream.

    ``tarfile``'s ``ExFileObject`` is seekable over an uncompressed TAR, so ``zipfile`` can
    read its central directory directly from the member stream — no intermediate buffering.
    """
    inner_zip = io.BytesIO()
    with zipfile.ZipFile(inner_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("payload.bin", CONTENT)
    inner_bytes = inner_zip.getvalue()

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as t:
        info = tarfile.TarInfo("inner.zip")
        info.size = len(inner_bytes)
        t.addfile(info, io.BytesIO(inner_bytes))
    tar_buf.seek(0)

    member = tarfile.open(fileobj=tar_buf, mode="r").extractfile("inner.zip")
    assert member is not None
    assert is_seekable(member)
    with zipfile.ZipFile(ensure_binaryio(member)) as inner:
        assert inner.read("payload.bin") == CONTENT


def test_raw_lzma2_member_stream_source() -> None:
    """A non-seekable member stream as a codec source (forward-only path)."""
    from tests.streams_util import compress_lzma2_raw, lzma2_raw_filters

    raw = compress_lzma2_raw(CONTENT)
    source = NonSeekableBytesIO(raw)
    with open_codec_stream(
        Codec.LZMA2, source, params=CodecParams(filters=lzma2_raw_filters())
    ) as stream:
        assert stream.read() == CONTENT
