"""7z coder method registry — single source for IDs, algorithms, and decode kind.

:class:`MethodKind` drives :func:`sevenzip_pipeline.plan_folder`:

- ``COPY`` / ``AES`` / ``BCJ2`` / ``SINGLE`` — self-explanatory stages
- ``LZMA_FAMILY`` — **not** "is LZMA": Delta and BCJ share this kind because they
  batch into the same liblzma / ``pybcj`` staging run as LZMA1/2

BCJ entries carry both short and long on-disk method ids (``aliases``) — 7-Zip
has historically written either form.
"""

from __future__ import annotations

import lzma
from dataclasses import dataclass
from enum import Enum, auto

from archivey.exceptions import UnsupportedFeatureError
from archivey.internal.streams.codecs import Codec
from archivey.types import CompressionAlgorithm


class MethodKind(Enum):
    """How :func:`sevenzip_pipeline.plan_folder` should stage this method.

    ``LZMA_FAMILY`` includes Delta/BCJ as well as LZMA1/2 — they compose into one
    staging run, not because they are LZMA codecs themselves.
    """

    COPY = auto()
    AES = auto()
    BCJ2 = auto()
    LZMA_FAMILY = auto()
    SINGLE = auto()


@dataclass(frozen=True, slots=True)
class SevenZipMethod:
    method_id: bytes
    algorithm: CompressionAlgorithm
    kind: MethodKind
    codec: Codec | None = None
    lzma_filter_id: int | None = None
    pybcj_attr: str | None = None
    aliases: tuple[bytes, ...] = ()


def _bcj(
    short: bytes,
    long: bytes,
    codec: Codec,
    filter_id: int,
    pybcj: str,
) -> SevenZipMethod:
    return SevenZipMethod(
        short,
        CompressionAlgorithm.BCJ,
        MethodKind.LZMA_FAMILY,
        codec=codec,
        lzma_filter_id=filter_id,
        pybcj_attr=pybcj,
        aliases=(long,),
    )


def _single(
    method_id: bytes, algo: CompressionAlgorithm, codec: Codec
) -> SevenZipMethod:
    return SevenZipMethod(method_id, algo, MethodKind.SINGLE, codec=codec)


_METHODS: tuple[SevenZipMethod, ...] = (
    SevenZipMethod(b"\x00", CompressionAlgorithm.STORED, MethodKind.COPY),
    SevenZipMethod(
        b"\x03\x01\x01",
        CompressionAlgorithm.LZMA,
        MethodKind.LZMA_FAMILY,
        codec=Codec.LZMA,
        lzma_filter_id=lzma.FILTER_LZMA1,
    ),
    SevenZipMethod(
        b"\x21",
        CompressionAlgorithm.LZMA2,
        MethodKind.LZMA_FAMILY,
        codec=Codec.LZMA2,
        lzma_filter_id=lzma.FILTER_LZMA2,
    ),
    SevenZipMethod(
        b"\x03",
        CompressionAlgorithm.DELTA,
        MethodKind.LZMA_FAMILY,
        codec=Codec.DELTA,
        lzma_filter_id=lzma.FILTER_DELTA,
    ),
    _bcj(b"\x04", b"\x03\x03\x01\x03", Codec.BCJ_X86, lzma.FILTER_X86, "BCJDecoder"),
    _bcj(
        b"\x05", b"\x03\x03\x02\x05", Codec.BCJ_PPC, lzma.FILTER_POWERPC, "PPCDecoder"
    ),
    _bcj(b"\x06", b"\x03\x03\x04\x01", Codec.BCJ_IA64, lzma.FILTER_IA64, "IA64Decoder"),
    _bcj(b"\x07", b"\x03\x03\x05\x01", Codec.BCJ_ARM, lzma.FILTER_ARM, "ARMDecoder"),
    _bcj(
        b"\x08",
        b"\x03\x03\x07\x01",
        Codec.BCJ_ARMT,
        lzma.FILTER_ARMTHUMB,
        "ARMTDecoder",
    ),
    _bcj(
        b"\x09", b"\x03\x03\x08\x05", Codec.BCJ_SPARC, lzma.FILTER_SPARC, "SparcDecoder"
    ),
    SevenZipMethod(b"\x03\x03\x01\x1b", CompressionAlgorithm.BCJ2, MethodKind.BCJ2),
    _single(b"\x04\x01\x08", CompressionAlgorithm.DEFLATE, Codec.DEFLATE),
    _single(b"\x04\x01\x09", CompressionAlgorithm.DEFLATE64, Codec.DEFLATE64),
    _single(b"\x04\x02\x02", CompressionAlgorithm.BZIP2, Codec.BZIP2),
    _single(b"\x04\xf7\x11\x01", CompressionAlgorithm.ZSTD, Codec.ZSTD),
    _single(b"\x04\xf7\x11\x02", CompressionAlgorithm.BROTLI, Codec.BROTLI),
    _single(b"\x04\xf7\x11\x04", CompressionAlgorithm.LZ4, Codec.LZ4),
    _single(b"\x03\x04\x01", CompressionAlgorithm.PPMD, Codec.PPMD),
    SevenZipMethod(b"\x06\xf1\x07\x01", CompressionAlgorithm.UNKNOWN, MethodKind.AES),
)

METHOD_COPY = _METHODS[0]
METHOD_LZMA = _METHODS[1]
METHOD_LZMA2 = _METHODS[2]
METHOD_DELTA = _METHODS[3]
METHOD_AES = _METHODS[-1]

_BY_ID: dict[bytes, SevenZipMethod] = {}
for _entry in _METHODS:
    _BY_ID[_entry.method_id] = _entry
    for _alias in _entry.aliases:
        _BY_ID[_alias] = _entry


def lookup(method_id: bytes) -> SevenZipMethod | None:
    return _BY_ID.get(method_id)


def require(method_id: bytes) -> SevenZipMethod:
    method = lookup(method_id)
    if method is None:
        raise UnsupportedFeatureError(
            f"Unsupported 7z coder method {_method_hex(method_id)}"
        )
    return method


def is_bcj(method_id: bytes) -> bool:
    entry = lookup(method_id)
    return entry is not None and entry.pybcj_attr is not None


def is_lzma_family(method_id: bytes) -> bool:
    entry = lookup(method_id)
    return entry is not None and entry.kind is MethodKind.LZMA_FAMILY


def _method_hex(method_id: bytes) -> str:
    return "0x" + method_id.hex()
