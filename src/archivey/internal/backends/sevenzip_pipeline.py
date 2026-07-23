"""7z folder decode pipeline — registry-driven linear coder chains.

A *folder* is a coder graph over one packed slice. This module only accepts a
**linear** 1-in/1-out chain (``bind_pairs == (i+1 → i)``, ``packed_indices == [0]``).

Decode order (packed → unpacked)::

    AES? → (COPY skipped) → SINGLE codecs | LZMA-family run | BCJ2 rejected

``MethodKind.LZMA_FAMILY`` means “participates in a liblzma / BCJ staging run”,
not “is LZMA”: Delta and BCJ are batched with LZMA1/2 here.

- LZMA2 ± Delta ± BCJ → one stdlib ``lzma`` raw filter chain
- LZMA1 + BCJ → capped LZMA1 stages + separate ``pybcj`` (BPO-21872 truncation)
- BCJ alone → ``pybcj`` stages
- BCJ2 (``0x0303011B``) → ``UnsupportedFeatureError`` (never garbage output)

Two phases: :func:`plan_folder` resolves stages (pure — no I/O); then
:func:`open_folder_pipeline` / :func:`_execute_stage` fold stages onto the packed
source. Encoded-header decode and the convenience :func:`parse_sevenzip_archive`
also live here (parser stays structure-only).
"""

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
    _MAX_NEXT_HEADER_SIZE,
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


# A folder's coder chain is decoded in two phases: `plan_folder` resolves it into an
# ordered list of these concrete stages (pure — no streams opened), then
# `_execute_stage` / `open_folder_pipeline` fold each stage onto the packed source.
# Keeping the branchy LZMA1+BCJ staging in the planner makes the decode flow
# inspectable and keeps I/O out of the decision.


@dataclass
class _AesStage:
    """AES-256 decryption of the stream."""

    coder: SevenZipCoder


@dataclass
class _CodecStage:
    """A single self-contained codec (Deflate, BZip2, Zstd, PPMd, …).

    ``unpack_size`` is the coder's output length from the folder header. It is passed
    through for codecs that need a bound (PPMd7 has no end mark); other codecs ignore it.

    ``pack_size`` is the coder's *input* length — the output of the preceding coder in
    the linear chain (or ``None`` when this coder consumes the packed slice directly,
    whose length ``open_codec_stream`` recovers from the sized source). Only PPMd reads
    it, to gate post-eof recovery on full pack delivery; it is load-bearing when the
    upstream stage is unsized (e.g. an AES decrypt stream over an encrypted PPMd folder),
    where the source length is otherwise unknowable.
    """

    codec: Codec
    properties: bytes | None
    unpack_size: int | None = None
    pack_size: int | None = None


@dataclass
class _LzmaChainStage:
    """One liblzma raw-filter chain (LZMA1/LZMA2 with any Delta/BCJ filters).

    ``cap_size`` bounds the decoded output with a ``SlicingStream``; it is set only
    for the stdlib LZMA1 runs inside an LZMA1+BCJ chain, where LZMA1-without-EOS can
    otherwise over-read on a trailing BCJ look-ahead (BPO-21872). ``None`` means no cap.
    """

    codec: Codec
    filters: list[dict]
    cap_size: int | None


@dataclass
class _BcjStage:
    """A single BCJ branch filter staged through pybcj (LZMA1+BCJ / BCJ-alone)."""

    pybcj_attr: str
    unpack_size: int


_Stage = _AesStage | _CodecStage | _LzmaChainStage | _BcjStage


def plan_folder(folder: SevenZipFolder) -> list[_Stage]:
    """Resolve a folder's coder chain into ordered decode stages, opening no streams.

    Runs all wiring validation (1-in/1-out, linear chain, BCJ2 reject) and groups the
    coders; :func:`open_folder_pipeline` turns the returned plan into a stream.
    """
    if any(
        coder.num_in_streams != 1 or coder.num_out_streams != 1
        for coder in folder.coders
    ):
        raise UnsupportedFeatureError(
            "7z folders with complex coder graphs are not supported"
        )
    _check_linear_coder_chain(folder)

    stages: list[_Stage] = []
    index = 0
    coders = folder.coders
    while index < len(coders):
        method = require(coders[index].method)
        if method.kind is MethodKind.COPY:
            index += 1
            continue
        if method.kind is MethodKind.BCJ2:
            raise UnsupportedFeatureError(
                "BCJ2-compressed 7z folders are not supported"
            )
        if method.kind is MethodKind.AES:
            stages.append(_AesStage(coders[index]))
            index += 1
            continue
        if method.kind is MethodKind.SINGLE:
            assert method.codec is not None
            # The coder's compressed input length is the preceding coder's output
            # (linear chain); for the first coder it consumes the packed slice, whose
            # length the sized source already carries, so leave it None there.
            input_size = folder.unpack_sizes[index - 1] if index > 0 else None
            stages.append(
                _CodecStage(
                    method.codec,
                    coders[index].properties,
                    unpack_size=folder.unpack_sizes[index],
                    pack_size=input_size,
                )
            )
            index += 1
            continue
        # LZMA_FAMILY (LZMA1/LZMA2/Delta/BCJ): batch the contiguous run, then plan it.
        run: list[SevenZipCoder] = []
        sizes: list[int] = []
        while index < len(coders) and is_lzma_family(coders[index].method):
            run.append(coders[index])
            sizes.append(folder.unpack_sizes[index])
            index += 1
        stages.extend(_plan_lzma_family(run, sizes))
    return stages


def _plan_lzma_family(
    run: list[SevenZipCoder], unpack_sizes: list[int]
) -> list[_Stage]:
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
        # BCJ alone (or after COPY / Deflate / …): each BCJ is its own pybcj stage.
        stages: list[_Stage] = []
        for coder, size in zip(run, unpack_sizes, strict=True):
            if not is_bcj(coder.method):
                raise UnsupportedFeatureError(
                    f"Unsupported non-LZMA 7z coder in BCJ run "
                    f"{_method_hex(coder.method)}"
                )
            stages.append(_bcj_stage(coder, size))
        return stages

    if has_lzma1 and has_bcj:
        # liblzma can silently truncate BCJ look-ahead when LZMA1 lacks EOS
        # (BPO-21872). Stage each stdlib LZMA1 (+ Delta, …) run capped to its output
        # size, then each BCJ through pybcj — never one combined liblzma chain.
        staged: list[_Stage] = []
        index = 0
        while index < len(run):
            if is_bcj(run[index].method):
                staged.append(_bcj_stage(run[index], unpack_sizes[index]))
                index += 1
                continue
            sub_run: list[SevenZipCoder] = []
            while index < len(run) and not is_bcj(run[index].method):
                sub_run.append(run[index])
                index += 1
            staged.append(_lzma_chain_stage(sub_run, cap_size=unpack_sizes[index - 1]))
        return staged

    # LZMA2 (± Delta ± BCJ) or LZMA1 (± Delta) with no separate BCJ staging: one chain.
    return [_lzma_chain_stage(run, cap_size=None)]


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


def _bcj_stage(coder: SevenZipCoder, unpack_size: int) -> _BcjStage:
    method = require(coder.method)
    if method.pybcj_attr is None:
        raise UnsupportedFeatureError(
            f"Unsupported 7z BCJ coder {_method_hex(coder.method)}"
        )
    return _BcjStage(method.pybcj_attr, unpack_size)


def _lzma_chain_stage(
    run: list[SevenZipCoder], *, cap_size: int | None
) -> _LzmaChainStage:
    has_lzma1 = any(lookup(c.method) is METHOD_LZMA for c in run)
    has_lzma2 = any(lookup(c.method) is METHOD_LZMA2 for c in run)
    # Decode order is outer-first; liblzma wants encode order → reversed(run).
    filters = [_lzma_filter(coder) for coder in reversed(run)]
    codec = Codec.LZMA if has_lzma1 and not has_lzma2 else Codec.LZMA2
    return _LzmaChainStage(codec, filters, cap_size)


def _execute_stage(
    stream: BinaryIO,
    stage: _Stage,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
) -> BinaryIO:
    """Open one planned stage on top of ``stream``. The only stream-opening code."""
    if isinstance(stage, _AesStage):
        return _open_aes_stage(
            stream, stage.coder, password=password, key_cache=key_cache
        )
    if isinstance(stage, _CodecStage):
        return open_codec_stream(
            stage.codec,
            stream,
            config=stream_config,
            params=CodecParams(
                properties=stage.properties,
                unpack_size=stage.unpack_size,
                pack_size=stage.pack_size,
            ),
            collector=collector,
            seekable=seekable,
        )
    if isinstance(stage, _LzmaChainStage):
        out = open_codec_stream(
            stage.codec,
            stream,
            config=stream_config,
            params=CodecParams(filters=stage.filters),
            collector=collector,
            seekable=seekable,
        )
        if stage.cap_size is not None:
            out = SlicingStream(out, length=stage.cap_size, own_source=True)
        return out
    return BcjFilterStream(
        stream,
        decoder_attr=stage.pybcj_attr,
        unpack_size=stage.unpack_size,
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
    """Compose a folder's coder chain into a single pull stream (plan, then fold)."""
    config = stream_config if stream_config is not None else DEFAULT_STREAM_CONFIG
    stages = plan_folder(folder)
    # Fail fast before opening any stream if a pybcj-staged BCJ filter is needed but
    # absent (LZMA2+BCJ folds BCJ into the liblzma chain and emits no _BcjStage).
    if any(isinstance(stage, _BcjStage) for stage in stages):
        _require_pybcj()
    stream: BinaryIO = source
    for stage in stages:
        stream = _execute_stage(
            stream,
            stage,
            password=password,
            key_cache=key_cache,
            stream_config=config,
            collector=collector,
            seekable=seekable,
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
        # Hostile archives can claim a multi-EiB folder unpack size. Cap before
        # ``read_exact`` / codec buffers allocate (Atheris: raw MemoryError).
        if uncompressed_size > _MAX_NEXT_HEADER_SIZE:
            raise CorruptionError(
                f"Encoded 7z header unpack size {uncompressed_size} exceeds the "
                f"{_MAX_NEXT_HEADER_SIZE}-byte parser limit"
            )
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
    # O8: encrypted headers never legitimately decode to zero file records.
    # Without this, ~0.3% of wrong-password py7zr salts slip through as empty.
    if header_encrypted and not block.files:
        raise EncryptionError("Password(s) rejected for the 7z header")
    return materialize_archive(signature, block, is_header_encrypted=header_encrypted)
