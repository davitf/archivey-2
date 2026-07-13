"""7z folder decode pipeline — registry-driven linear coder chains."""

from __future__ import annotations

import lzma
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_methods import (
    METHOD_DELTA,
    METHOD_LZMA,
    METHOD_LZMA2,
    MethodKind,
    _method_hex,
    is_bcj,
    is_lzma_family,
    lookup,
    require,
)
from archivey.internal.backends.sevenzip_parser import (
    EncodedHeader,
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFolder,
    encoded_folder_slices,
    folder_is_encrypted,
)
from archivey.internal.config import DEFAULT_STREAM_CONFIG, StreamConfig
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.crypto import SevenZipKeyCache, open_aes_decrypt_stream
from archivey.internal.streams.decompress import BcjFilterStream
from archivey.internal.streams.streamtools import SlicingStream, read_exact

# stdlib exposes no public decoder for a raw LZMA1/LZMA2 property blob → filter dict;
# py7zr relies on the same private `lzma._decode_filter_properties`. Bind once at import.
_raw_decode_filter_properties = getattr(lzma, "_decode_filter_properties", None)
if _raw_decode_filter_properties is None:  # pragma: no cover
    raise ImportError(
        "This Python's `lzma` module no longer exposes `_decode_filter_properties`, which "
        "archivey's native 7z reader needs to decode raw LZMA1/LZMA2 coder properties. "
        "Please report this to archivey (with your Python version)."
    )
_decode_filter_properties: Callable[[int, bytes], dict] = _raw_decode_filter_properties


@dataclass(frozen=True, slots=True)
class _CoderStage:
    """One pipeline stage: a single coder or a batched LZMA-family run."""

    kind: MethodKind
    coders: tuple[SevenZipCoder, ...]
    unpack_sizes: tuple[int, ...]


def group_coders(folder: SevenZipFolder) -> list[_CoderStage]:
    """Group folder coders into pipeline stages (COPY skipped; LZMA_FAMILY batched)."""
    stages: list[_CoderStage] = []
    index = 0
    while index < len(folder.coders):
        coder = folder.coders[index]
        method = require(coder.method)
        if method.kind is MethodKind.COPY:
            index += 1
            continue
        if method.kind is MethodKind.LZMA_FAMILY:
            run: list[SevenZipCoder] = []
            sizes: list[int] = []
            while index < len(folder.coders) and is_lzma_family(
                folder.coders[index].method
            ):
                run.append(folder.coders[index])
                sizes.append(folder.unpack_sizes[index])
                index += 1
            stages.append(_CoderStage(MethodKind.LZMA_FAMILY, tuple(run), tuple(sizes)))
            continue
        stages.append(_CoderStage(method.kind, (coder,), (folder.unpack_sizes[index],)))
        index += 1
    return stages


def _check_linear_coder_chain(folder: SevenZipFolder) -> None:
    """Verify coders form a single linear chain in list order."""
    expected_bind = {(i + 1, i) for i in range(len(folder.coders) - 1)}
    if folder.packed_indices != [0] or set(folder.bind_pairs) != expected_bind:
        raise UnsupportedFeatureError(
            "7z folders with non-linear coder wiring are not supported"
        )


def _require_pybcj() -> None:
    try:
        import bcj  # noqa: F401
    except ImportError as exc:
        raise PackageNotInstalledError(
            "The 'pybcj' package is required for LZMA1+BCJ 7z folders "
            "(install the '7z' extra)."
        ) from exc


def _decode_lzma_properties(coder: SevenZipCoder, filter_id: int) -> dict:
    if coder.properties is None:
        return {"id": filter_id}
    try:
        return _decode_filter_properties(filter_id, coder.properties)
    except (lzma.LZMAError, ValueError) as exc:
        raise CorruptionError(
            f"Malformed 7z LZMA coder properties for {_method_hex(coder.method)}"
        ) from exc


def _lzma_filter(coder: SevenZipCoder) -> dict:
    method = require(coder.method)
    if method is METHOD_LZMA or method is METHOD_LZMA2:
        assert method.lzma_filter_id is not None
        return _decode_lzma_properties(coder, method.lzma_filter_id)
    if method is METHOD_DELTA:
        if coder.properties is None:
            return {"id": lzma.FILTER_DELTA}
        if len(coder.properties) != 1:
            raise CorruptionError("Malformed 7z Delta coder properties")
        return {"id": lzma.FILTER_DELTA, "dist": coder.properties[0] + 1}
    if method.lzma_filter_id is not None and method.pybcj_attr is not None:
        return {"id": method.lzma_filter_id}
    raise UnsupportedFeatureError(
        f"Unsupported 7z LZMA-family coder {_method_hex(coder.method)}"
    )


def _open_aes_stage(
    source: BinaryIO,
    coder: SevenZipCoder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
) -> BinaryIO:
    if password is None:
        raise EncryptionError("Password required to decrypt this 7z folder")
    if coder.properties is None:
        raise CorruptionError("7z AES coder is missing properties")
    try:
        params = key_cache.aes_params_from_properties(password, coder.properties)
    except ValueError as exc:
        raise CorruptionError(f"Malformed 7z AES properties: {exc}") from exc
    return open_aes_decrypt_stream(source, params)


def _open_bcj_stage(
    source: BinaryIO,
    coder: SevenZipCoder,
    *,
    unpack_size: int,
    seekable: bool,
) -> BinaryIO:
    method = require(coder.method)
    if method.pybcj_attr is None:
        raise UnsupportedFeatureError(
            f"Unsupported 7z BCJ coder {_method_hex(coder.method)}"
        )
    return BcjFilterStream(
        source,
        decoder_attr=method.pybcj_attr,
        unpack_size=unpack_size,
        seekable=seekable,
    )


def _open_lzma_combined(
    source: BinaryIO,
    run: list[SevenZipCoder],
    *,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
) -> BinaryIO:
    has_lzma1 = any(lookup(c.method) is METHOD_LZMA for c in run)
    has_lzma2 = any(lookup(c.method) is METHOD_LZMA2 for c in run)
    # Decode order is outer-first; liblzma wants encode order → reversed(run).
    filters = [_lzma_filter(coder) for coder in reversed(run)]
    codec = Codec.LZMA if has_lzma1 and not has_lzma2 else Codec.LZMA2
    return open_codec_stream(
        codec,
        source,
        config=stream_config,
        params=CodecParams(filters=filters),
        collector=collector,
        seekable=seekable,
    )


def _open_lzma_family(
    source: BinaryIO,
    run: list[SevenZipCoder],
    unpack_sizes: list[int],
    *,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
) -> BinaryIO:
    if len(run) != len(unpack_sizes):
        raise CorruptionError("7z LZMA-family run length does not match unpack sizes")
    has_lzma1 = any(lookup(c.method) is METHOD_LZMA for c in run)
    has_lzma2 = any(lookup(c.method) is METHOD_LZMA2 for c in run)
    has_bcj = any(is_bcj(c.method) for c in run)
    if has_lzma1 and has_lzma2:
        raise UnsupportedFeatureError(
            "Mixed LZMA1+LZMA2 7z coder chains are unsupported"
        )
    if has_bcj and not has_lzma1 and not has_lzma2:
        # BCJ alone (or after COPY / Deflate / …): stage via pybcj.
        _require_pybcj()
        stream: BinaryIO = source
        for index, coder in enumerate(run):
            if not is_bcj(coder.method):
                raise UnsupportedFeatureError(
                    f"Unsupported non-LZMA 7z coder in BCJ run "
                    f"{_method_hex(coder.method)}"
                )
            stream = _open_bcj_stage(
                stream, coder, unpack_size=unpack_sizes[index], seekable=seekable
            )
        return stream
    if has_lzma1 and has_bcj:
        # liblzma can silently truncate BCJ look-ahead when LZMA1 lacks EOS (BPO-21872).
        # Stage: stdlib LZMA1 (+ Delta, etc.), then pybcj.
        _require_pybcj()
        stream = source
        index = 0
        while index < len(run):
            coder = run[index]
            if is_bcj(coder.method):
                stream = _open_bcj_stage(
                    stream, coder, unpack_size=unpack_sizes[index], seekable=seekable
                )
                index += 1
                continue
            sub_run: list[SevenZipCoder] = []
            while index < len(run) and not is_bcj(run[index].method):
                sub_run.append(run[index])
                index += 1
            stream = _open_lzma_combined(
                stream,
                sub_run,
                stream_config=stream_config,
                collector=collector,
                seekable=seekable,
            )
            stream = SlicingStream(
                stream, length=unpack_sizes[index - 1], own_source=True
            )
        return stream
    return _open_lzma_combined(
        source,
        run,
        stream_config=stream_config,
        collector=collector,
        seekable=seekable,
    )


def open_folder_pipeline(
    source: BinaryIO,
    folder: SevenZipFolder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
    seekable: bool = False,
) -> BinaryIO:
    """Compose a folder's coder chain into a single pull stream."""
    if any(
        coder.num_in_streams != 1 or coder.num_out_streams != 1
        for coder in folder.coders
    ):
        raise UnsupportedFeatureError(
            "7z folders with complex coder graphs are not supported"
        )
    _check_linear_coder_chain(folder)
    config = stream_config if stream_config is not None else DEFAULT_STREAM_CONFIG
    stream: BinaryIO = source

    for stage in group_coders(folder):
        if stage.kind is MethodKind.BCJ2:
            raise UnsupportedFeatureError(
                "BCJ2-compressed 7z folders are not supported"
            )
        if stage.kind is MethodKind.AES:
            stream = _open_aes_stage(
                stream, stage.coders[0], password=password, key_cache=key_cache
            )
            continue
        if stage.kind is MethodKind.LZMA_FAMILY:
            stream = _open_lzma_family(
                stream,
                list(stage.coders),
                list(stage.unpack_sizes),
                stream_config=config,
                collector=collector,
                seekable=seekable,
            )
            continue
        if stage.kind is MethodKind.SINGLE:
            coder = stage.coders[0]
            method = require(coder.method)
            assert method.codec is not None
            stream = open_codec_stream(
                method.codec,
                stream,
                config=config,
                params=CodecParams(properties=coder.properties),
                collector=collector,
                seekable=seekable,
            )
            continue
        raise UnsupportedFeatureError(
            f"Unsupported 7z coder method {_method_hex(stage.coders[0].method)}"
        )
    return stream


def decode_folder_to_bytes(
    source: BinaryIO,
    folder: SevenZipFolder,
    *,
    compressed_size: int,
    uncompressed_size: int,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> bytes:
    """Fully decode one folder's packed stream into memory and CRC-check it."""
    stream = open_folder_pipeline(
        SlicingStream(source, 0, compressed_size),
        folder,
        password=password,
        key_cache=key_cache,
        stream_config=stream_config,
        collector=collector,
    )
    try:
        decoded = read_exact(stream, uncompressed_size)
        if len(decoded) != uncompressed_size:
            raise TruncatedError("7z folder is truncated after decoding")
        if folder.digest_defined and folder.crc is not None:
            if zlib.crc32(decoded) & 0xFFFFFFFF != folder.crc & 0xFFFFFFFF:
                raise CorruptionError("Decoded 7z folder CRC mismatch")
        return decoded
    finally:
        stream.close()


def decode_encoded_header(
    archive_fp: BinaryIO,
    encoded: EncodedHeader,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> bytes:
    """Materialize an ENCODED_HEADER's packed folders to plaintext header bytes."""
    decoded = bytearray()
    for (
        folder,
        absolute_offset,
        compressed_size,
        uncompressed_size,
    ) in encoded_folder_slices(encoded):
        source = SlicingStream(archive_fp, absolute_offset, compressed_size)
        decoded.extend(
            decode_folder_to_bytes(
                source,
                folder,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                password=password,
                key_cache=key_cache,
                stream_config=stream_config,
                collector=collector,
            )
        )
    return bytes(decoded)


def encoded_header_needs_password(encoded: EncodedHeader) -> bool:
    folders = encoded.streams.folders or []
    return any(folder_is_encrypted(folder) for folder in folders)


def parse_sevenzip_archive(
    fp: BinaryIO,
    *,
    password: bytes | None = None,
    key_cache: SevenZipKeyCache | None = None,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> SevenZipArchive:
    """Parse a 7z archive end-to-end (plain or encoded header).

    Used by fuzz harnesses and tests. The reader uses the same two-phase flow with
    password-candidate prompting instead of a single ``password``.
    """
    from archivey.internal.backends.sevenzip_parser import (
        PlainHeader,
        empty_archive,
        materialize_archive,
        parse_header_block,
        read_signature_and_next_header,
    )

    cache = key_cache if key_cache is not None else SevenZipKeyCache()
    signature = read_signature_and_next_header(fp)
    if not signature.header_data:
        return empty_archive(signature)

    block = parse_header_block(signature.header_data)
    header_encrypted = False
    while isinstance(block, EncodedHeader):
        header_encrypted = header_encrypted or encoded_header_needs_password(block)
        decoded = decode_encoded_header(
            fp,
            block,
            password=password,
            key_cache=cache,
            stream_config=stream_config,
            collector=collector,
        )
        block = parse_header_block(decoded)
    assert isinstance(block, PlainHeader)
    return materialize_archive(signature, block, is_header_encrypted=header_encrypted)
