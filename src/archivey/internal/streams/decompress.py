"""Thin ``BaseDecoder`` adapters: zlib/deflate, Brotli, PPMd, BCJ, Deflate64.

Not the seekable engine — that is :mod:`.decompressor_stream`. Each class here
implements :class:`~archivey.internal.streams.decompressor_stream.Decoder`; helpers
return a ``DecompressorStream`` wrapping the adapter (no per-codec stream
subclasses). XZ / lzip / unix-compress decoders live in their own modules for the
same reason (larger index/LZW logic).
"""

from __future__ import annotations

import os
import zlib
from typing import Any, BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)

_GZIP_MAGIC = b"\x1f\x8b"
_GZIP_WBITS = 16 + zlib.MAX_WBITS


class ZlibDecoder(BaseDecoder):
    """Inflate a raw-deflate or zlib-wrapped stream via ``zlib.decompressobj``."""

    def __init__(self, wbits: int = -15) -> None:
        self._wbits = wbits
        self._decomp = zlib.decompressobj(wbits)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> ZlibDecoder:
        del point, inner
        return ZlibDecoder(self._wbits)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        # unconsumed_tail holds input not yet consumed under a prior max_length cap;
        # prepend it exactly once (mirrors gzip._GzipReader).
        data = self._decomp.unconsumed_tail + chunk
        if not data:
            return DecodeOut(b"")
        if max_length < 0:
            return DecodeOut(self._decomp.decompress(data))
        return DecodeOut(self._decomp.decompress(data, max_length))

    def flush(self) -> DecodeOut:
        if self._decomp.unconsumed_tail:
            out = self._decomp.decompress(self._decomp.unconsumed_tail)
            leftover = out + self._decomp.flush()
        else:
            leftover = self._decomp.flush()
        if not self._decomp.eof:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(leftover)

    @property
    def finished(self) -> bool:
        return self._decomp.eof

    @property
    def needs_input(self) -> bool:
        return not self._decomp.unconsumed_tail


class GzipDecoder(BaseDecoder):
    """gzip-window inflate with GzipFile-parity multi-member chaining.

    Uses ``wbits=16+MAX_WBITS`` so zlib validates CRC/ISIZE. After each member,
    strips leading NUL padding from ``unused_data`` / retained input, then:
    empty → clean EOF; ``1f 8b`` → new ``decompressobj`` and continue; anything
    else → deferred :class:`~archivey.exceptions.CorruptionError` (trailing junk /
    partial magic at true EOF) via :attr:`pending_error`, after returning any
    already-decoded member bytes — same deliver-then-raise shape as truncation.
    Cross-``feed`` NUL runs and a lone trailing ``1f`` are retained until the next
    header (or ``flush``) resolves them.

    Mid-member ``max_length`` remainder stays in ``decompressobj.unconsumed_tail``
    (same as :class:`ZlibDecoder`); ``_retained`` is only for post-member bytes.
    """

    def __init__(self) -> None:
        self._decomp = zlib.decompressobj(_GZIP_WBITS)
        # Post-member bytes not yet resolved (NUL padding / next magic / junk).
        # Never store unconsumed_tail here — that lives on the decompressobj.
        self._retained = b""
        self._between_members = False
        self._finished = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> GzipDecoder:
        del point, inner
        return GzipDecoder()

    def _arm_trailing_junk(self, data: bytes) -> None:
        """Defer trailing-junk CorruptionError so already-decoded bytes can return.

        Raising from ``feed``/``flush`` would discard the local output buffer (and
        any prior members in the same call). Mirror truncation: arm pending_error
        and let the stream raise on the next empty ``read`` / ``readall``.
        """
        self._pending_error = CorruptionError(
            "Trailing non-gzip data after a completed gzip member "
            f"(starts with {data[:8]!r})"
        )
        self._retained = b""
        self._between_members = False
        self._finished = True

    def _resolve_between(self, data: bytes) -> bytes:
        """Strip NULs; start next member, retain partial magic, arm junk, or wait."""
        i = 0
        while i < len(data) and data[i] == 0:
            i += 1
        data = data[i:]
        if not data:
            self._between_members = True
            self._retained = b""
            return b""
        if data.startswith(_GZIP_MAGIC):
            self._decomp = zlib.decompressobj(_GZIP_WBITS)
            self._between_members = False
            self._retained = b""
            return data
        if data == b"\x1f":
            self._between_members = True
            self._retained = data
            return b""
        self._arm_trailing_junk(data)
        return b""

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if self._finished or self._pending_error is not None:
            return DecodeOut(b"")

        if self._between_members:
            data = self._retained + chunk
            self._retained = b""
        else:
            data = self._decomp.unconsumed_tail + chunk

        output = bytearray()
        while True:
            if self._pending_error is not None:
                break
            if max_length >= 0 and len(output) >= max_length:
                if self._between_members and data:
                    self._retained = data
                break

            if self._between_members:
                data = self._resolve_between(data)
                if self._pending_error is not None:
                    break
                if self._retained or not data:
                    # Partial magic retained, or only NULs/empty — need more input.
                    break
                continue

            if not data:
                break

            limit = max_length - len(output) if max_length >= 0 else -1
            if limit == 0:
                break
            try:
                if limit < 0:
                    produced = self._decomp.decompress(data)
                else:
                    produced = self._decomp.decompress(data, limit)
            except zlib.error as e:
                # Corrupt deflate body (bad CRC/data check inside a member). Raise
                # CorruptionError here so a raw GzipDecompressorStream is consistent
                # with flush() and does not leak zlib.error (GzipCodec.translate maps
                # it too, but the decoder must stand on its own).
                raise CorruptionError(f"Error reading gzip stream: {e!r}") from e
            output.extend(produced)

            if self._decomp.eof:
                data = self._decomp.unused_data
                self._between_members = True
                continue

            # More compressed input remains under a max_length cap — leave it in
            # unconsumed_tail for the next feed (do not copy into _retained).
            data = self._decomp.unconsumed_tail
            if data and produced and (max_length < 0 or len(output) < max_length):
                continue
            break

        return DecodeOut(bytes(output))

    def flush(self) -> DecodeOut:
        if self._finished and self._pending_error is not None:
            return DecodeOut(b"")
        out = bytearray()
        # Drain mid-member unconsumed_tail / continue member chaining with no new input.
        drained = self.feed(b"")
        out.extend(drained.data)
        if self._pending_error is not None:
            return DecodeOut(bytes(out))

        if self._between_members:
            data = self._retained
            self._retained = b""
            i = 0
            while i < len(data) and data[i] == 0:
                i += 1
            data = data[i:]
            if not data:
                self._finished = True
                return DecodeOut(bytes(out))
            if data.startswith(_GZIP_MAGIC):
                self._decomp = zlib.decompressobj(_GZIP_WBITS)
                self._between_members = False
                try:
                    produced = self._decomp.decompress(data)
                    out.extend(produced)
                    if self._decomp.unconsumed_tail:
                        out.extend(
                            self._decomp.decompress(self._decomp.unconsumed_tail)
                        )
                    if not self._decomp.eof:
                        out.extend(self._decomp.flush())
                except zlib.error as e:
                    raise CorruptionError(f"Error reading gzip stream: {e!r}") from e
                if not self._decomp.eof:
                    self._pending_error = TruncatedError("gzip stream is truncated")
                    return DecodeOut(bytes(out))
                trailing = self._decomp.unused_data
                j = 0
                while j < len(trailing) and trailing[j] == 0:
                    j += 1
                trailing = trailing[j:]
                if trailing:
                    self._arm_trailing_junk(trailing)
                    return DecodeOut(bytes(out))
                self._finished = True
                return DecodeOut(bytes(out))
            self._arm_trailing_junk(data)
            return DecodeOut(bytes(out))

        # Mid-member compressed EOF.
        try:
            if self._decomp.unconsumed_tail:
                out.extend(self._decomp.decompress(self._decomp.unconsumed_tail))
            out.extend(self._decomp.flush())
        except zlib.error as e:
            raise CorruptionError(f"Error reading gzip stream: {e!r}") from e
        if not self._decomp.eof:
            self._pending_error = TruncatedError("gzip stream is truncated")
        else:
            # Completed final member exactly at EOF.
            trailing = self._decomp.unused_data
            j = 0
            while j < len(trailing) and trailing[j] == 0:
                j += 1
            trailing = trailing[j:]
            if trailing == b"\x1f" or (
                trailing and not trailing.startswith(_GZIP_MAGIC)
            ):
                self._arm_trailing_junk(trailing)
            elif trailing.startswith(_GZIP_MAGIC):
                self._pending_error = TruncatedError("gzip stream is truncated")
            else:
                self._finished = True
        return DecodeOut(bytes(out))

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def needs_input(self) -> bool:
        if self._pending_error is not None or self._finished:
            return True
        if self._decomp.unconsumed_tail:
            return False
        # Full next-member prefix retained — drain without reading more.
        if self._retained.startswith(_GZIP_MAGIC):
            return False
        return True


class BrotliDecoder(BaseDecoder):
    """Decode a raw Brotli stream via the ``brotli`` package's incremental decompressor.

    The ``brotli`` import is local because it's an optional dependency with no type stubs;
    the codec layer's ``_open_brotli`` gates on its presence before constructing this, so
    the import here always succeeds.

    Brotli ≥1.2.0 exposes ``process(..., output_buffer_limit=)`` and
    ``can_accept_more_data()`` (CVE-2025-6176 mitigation). The limit is block-granular
    (observed floor ~32 KiB), not a hard byte cap, but it stops a single ``process``
    from materializing multi-megabyte bombs on ``read(1)``.
    """

    def __init__(self) -> None:
        import brotli

        self._decomp: Any = brotli.Decompressor()
        self._pending = b""
        # True while a prior budgeted process may still have output to drain via
        # process(b"", output_buffer_limit=…).
        self._drain_budgeted = False
        self._supports_output_limit = callable(
            getattr(self._decomp, "can_accept_more_data", None)
        )

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BrotliDecoder:
        del point, inner
        return BrotliDecoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if max_length < 0 or not self._supports_output_limit:
            self._drain_budgeted = False
            if not data:
                return DecodeOut(b"")
            return DecodeOut(self._decomp.process(data))

        can_accept = bool(self._decomp.can_accept_more_data())
        if not can_accept:
            # Limit reached on a prior call: only empty process is legal until
            # can_accept_more_data() flips true again.
            self._pending = data
            out = self._decomp.process(b"", output_buffer_limit=max_length)
        elif data:
            out = self._decomp.process(data, output_buffer_limit=max_length)
        elif self._drain_budgeted:
            out = self._decomp.process(b"", output_buffer_limit=max_length)
        else:
            return DecodeOut(b"")

        finished = bool(self._decomp.is_finished())
        # Keep draining while output is flowing or the decoder refuses more input.
        self._drain_budgeted = (not finished) and (
            len(out) > 0 or not bool(self._decomp.can_accept_more_data())
        )
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        # Brotli decodes eagerly; there is nothing buffered to flush at EOF.
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return bool(self._decomp.is_finished())

    @property
    def needs_input(self) -> bool:
        if self._pending:
            return False
        if self._supports_output_limit and not bool(
            self._decomp.can_accept_more_data()
        ):
            return False
        return not self._drain_budgeted


# Per-call output request for PPMd8 decodes without a declared size; 64 KiB matches
# the stream layer's read chunk. NOTE: a bound is NOT what makes decoding safe — on
# pyppmd 1.3.x any request exceeding the stream's true remaining output by ≳64 KiB
# corrupts the heap even without -1 (measured: +64/+4096 over → 0/20 crashes,
# +65536 over → 13/20). PPMd8 is safe here because its end mark stops the native
# worker on valid data before any over-decode; PPMd7 has no end mark, which is why
# PpmdDecoder refuses to decode it without ``unpack_size`` at all.
_PPMD_UNSIZED_DECODE_CHUNK = 65536


class PpmdDecoder(BaseDecoder):
    """Decode a PPMd stream via ``pyppmd``.

    Variant 7 (``Ppmd7Decoder``) is the 7z var.H coder. Variant 8 (``Ppmd8Decoder``)
    is ZIP method 98 / WinZip ZIPX PPMd, which also carries a restore-method parameter.

    ``unpack_size`` (the 7z folder / ZIP member size) is passed through as
    ``max_length`` on every ``decode`` call, matching py7zr's
    ``PpmdDecompressor.decompress(..., max_length)``. This is load-bearing on pyppmd
    1.3.x: the native worker thread decodes as many symbols as the request allows, and
    running it materially past the true end of stream corrupts the heap (Linux
    ``malloc`` abort/SIGSEGV, Windows ``STATUS_HEAP_CORRUPTION``) — measured for
    ``-1`` and for sized requests ≳64 KiB beyond the real payload alike. Requesting
    exactly the remaining output is the safe contract; see
    ``docs/internal/known-issues.md`` and ``docs/internal/pyppmd-upstream-report.md``.

    Because PPMd7 has no end mark, there is no safe request size without knowing the
    payload length — so ``unpack_size`` is **required** for variant 7 (the 7z header
    always provides it). PPMd8 carries an end mark that stops the native worker on
    valid data, so variant 8 may be unsized; it is then decoded via bounded
    :data:`_PPMD_UNSIZED_DECODE_CHUNK` requests in a drain loop, never ``-1``.

    At compressed EOF, PPMd may still need an extra NUL input byte when the encoder
    omitted a trailing null (documented by pyppmd). ``flush`` feeds exactly one NUL
    with the remaining ``max_length``, same as py7zr / the pyppmd PyPI sample; if
    output is still short of ``unpack_size`` after that, the stream is truncated.
    """

    def __init__(
        self,
        *,
        order: int,
        mem_size: int,
        variant: int = 7,
        restore_method: int = 0,
        unpack_size: int | None = None,
    ) -> None:
        import pyppmd

        if variant != 8 and unpack_size is None:
            raise ValueError(
                "PPMd7 (7z var.H) requires unpack_size: the format has no end mark, "
                "and decoding without the exact output bound runs pyppmd past the "
                "end of stream (native heap corruption on 1.3.x — see "
                "docs/internal/known-issues.md)"
            )
        self._order = order
        self._mem_size = mem_size
        self._variant = variant
        self._restore_method = restore_method
        self._unpack_size = unpack_size
        self._produced = 0
        if variant == 8:
            self._decomp: Any = pyppmd.Ppmd8Decoder(order, mem_size, restore_method)
        else:
            self._decomp = pyppmd.Ppmd7Decoder(order, mem_size)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> PpmdDecoder:
        del point, inner
        return PpmdDecoder(
            order=self._order,
            mem_size=self._mem_size,
            variant=self._variant,
            restore_method=self._restore_method,
            unpack_size=self._unpack_size,
        )

    def _max_length(self) -> int:
        if self._unpack_size is None:
            return -1
        return max(0, self._unpack_size - self._produced)

    def _decode_unsized(self, data: bytes) -> bytes:
        # Unsized PPMd8 only (PPMd7 without a size is rejected in __init__). Never
        # hand pyppmd max_length=-1; request bounded chunks and drain the internally
        # buffered input instead. Valid PPMd8 stops at its end mark before any
        # over-decode; the bound avoids the -1 allocation path and caps the damage
        # on corrupt data. Stop when the decoder needs more input (await the next
        # feed), hits eof, or goes quiet.
        parts: list[bytes] = []
        chunk = self._decomp.decode(data, _PPMD_UNSIZED_DECODE_CHUNK)
        while chunk:
            parts.append(chunk)
            if self._decomp.eof or getattr(self._decomp, "needs_input", False):
                break
            chunk = self._decomp.decode(b"", _PPMD_UNSIZED_DECODE_CHUNK)
        return b"".join(parts)

    def _decode(self, data: bytes, max_length: int) -> bytes:
        if max_length == 0:
            return b""
        # Decoding after native EOF is trailing garbage at best (and the crashy
        # runaway path on pyppmd 1.3.x when unbounded) — drop the input instead.
        if self._decomp.eof and max_length < 0:
            return b""
        # Empty input + needs_input (pre-EOF): documented extra NUL (pyppmd / py7zr).
        if (
            not data
            and getattr(self._decomp, "needs_input", False)
            and not self._decomp.eof
        ):
            data = b"\0"
        if max_length < 0:
            return self._decode_unsized(data)
        return self._decomp.decode(data, max_length)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        # Honour both the container unpack_size cap and the stream-layer read budget.
        unpack_cap = self._max_length()
        if max_length >= 0 and unpack_cap >= 0:
            limit = min(max_length, unpack_cap)
        elif max_length >= 0:
            limit = max_length
        else:
            limit = unpack_cap
        out = self._decode(chunk, limit)
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        # A stream whose encoder omitted the trailing byte still reports needs_input
        # at compressed EOF; the documented recovery (pyppmd docs / py7zr) is exactly
        # one extra NUL, bounded by the remaining size. Never inject fabricated input
        # in a loop — on truncated data that decodes garbage through the native model,
        # which can silently complete a member and (on pyppmd 1.3.x, if unbounded)
        # corrupt the heap. Anything still missing after the single NUL is truncation,
        # surfaced via pending_error.
        max_length = self._max_length()
        if max_length == 0:
            return DecodeOut(b"")
        out = b""
        if not self._decomp.eof and getattr(self._decomp, "needs_input", False):
            out = self._decode(b"\0", max_length)
            self._produced += len(out)
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        # Prefer the container size when known: it detects truncation (short output
        # at compressed EOF) and ends the member exactly at its boundary — PPMd7 has
        # no end mark, so native eof alone cannot do either.
        if self._unpack_size is not None:
            return self._produced >= self._unpack_size
        return bool(self._decomp.eof)

    @property
    def needs_input(self) -> bool:
        return bool(getattr(self._decomp, "needs_input", True))


class BcjDecoder(BaseDecoder):
    """Apply a ``pybcj`` BCJ branch filter to an already-decompressed byte stream."""

    def __init__(self, *, decoder_attr: str, unpack_size: int) -> None:
        import bcj

        self._decoder_attr = decoder_attr
        self._unpack_size = unpack_size
        self._produced = 0
        decoder_cls = getattr(bcj, decoder_attr)
        self._decomp: Any = decoder_cls(unpack_size)
        self._pending = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BcjDecoder:
        del point, inner
        return BcjDecoder(
            decoder_attr=self._decoder_attr, unpack_size=self._unpack_size
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if not data:
            return DecodeOut(b"")
        if max_length >= 0 and len(data) > max_length:
            # BCJ is a filter (near 1:1); feed only what the caller budget allows and
            # retain the rest — bounds peak buffer without a native max_length API.
            self._pending = data[max_length:]
            data = data[:max_length]
        out = self._decomp.decode(data)
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        out = self._decomp.decode(self._pending)
        self._pending = b""
        out2 = self._decomp.decode(b"")
        self._produced += len(out) + len(out2)
        leftover = out + out2
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(leftover)

    @property
    def finished(self) -> bool:
        return self._produced >= self._unpack_size

    @property
    def needs_input(self) -> bool:
        return not self._pending


class Deflate64Decoder(BaseDecoder):
    """Decode a Deflate64 stream via ``inflate64.Inflater``.

    ``inflate64`` has no output-size parameter: one ``inflate`` of a small
    highly-compressible feed can still allocate the full expansion. When the
    stream passes ``max_length >= 0``, feed compressed input in small steps
    (see ``_BUDGETED_FEED``) and retain any overshoot in ``_pending_out`` so
    ``read(n)`` peak buffers stay near the caller's budget.

    Feed-size tradeoff on a 100 MiB zeros Deflate64 bomb (per-call max_out /
    throughput): 1→514 B / ~320 MiB/s; 64→19 KiB / ~700 MiB/s; 256→70 KiB /
    ~710 MiB/s; 64 KiB→18 MiB / ~460 MiB/s. 64 keeps peaks under a 64 KiB
    read budget while recovering most of the speed of larger feeds.
    """

    # Compressed bytes per inflate() under a max_length budget. See class docstring.
    _BUDGETED_FEED = 64

    def __init__(self) -> None:
        import inflate64

        self._decomp: Any = inflate64.Inflater()
        self._pending = b""
        self._pending_out = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Deflate64Decoder:
        del point, inner
        return Deflate64Decoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if max_length < 0:
            if self._pending_out:
                data = self._pending_out + (self._decomp.inflate(data) if data else b"")
                self._pending_out = b""
                return DecodeOut(data)
            if not data:
                return DecodeOut(b"")
            return DecodeOut(self._decomp.inflate(data))

        out = bytearray()
        if self._pending_out:
            take = min(len(self._pending_out), max_length)
            out += self._pending_out[:take]
            self._pending_out = self._pending_out[take:]
            if len(out) >= max_length:
                self._pending = data
                return DecodeOut(bytes(out))

        step = self._BUDGETED_FEED
        while data and len(out) < max_length:
            produced = self._decomp.inflate(data[:step])
            data = data[step:]
            room = max_length - len(out)
            if len(produced) > room:
                out += produced[:room]
                self._pending_out = produced[room:]
                break
            out += produced
        self._pending = data
        return DecodeOut(bytes(out))

    def flush(self) -> DecodeOut:
        # Flush remaining state with an empty feed (mirrors py7zr's Deflate64Decompressor).
        if self._pending_out:
            out = self._pending_out
            self._pending_out = b""
            if not self._decomp.eof:
                out += self._decomp.inflate(b"")
        elif self._decomp.eof:
            out = b""
        else:
            out = self._decomp.inflate(b"")
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return bool(self._decomp.eof) and not self._pending_out

    @property
    def needs_input(self) -> bool:
        return not self._pending and not self._pending_out


def ZlibDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    wbits: int = -15,
) -> DecompressorStream:
    """Inflate a raw-deflate or zlib-wrapped stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: ZlibDecoder(wbits))


def GzipDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
) -> DecompressorStream:
    """Inflate a gzip stream with multi-member chaining (forward-only; O(n) rewind)."""
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: GzipDecoder(),
        codec_name="gzip",
    )


def BrotliDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
) -> DecompressorStream:
    """Decode a raw Brotli stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: BrotliDecoder())


def PpmdDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    order: int,
    mem_size: int,
    variant: int = 7,
    restore_method: int = 0,
    unpack_size: int | None = None,
) -> DecompressorStream:
    """Decode a PPMd stream (forward-only).

    ``variant=7`` is 7z PPMd var.H; ``variant=8`` is ZIP method 98 (PPMd8).
    ``unpack_size`` is required for PPMd7 (no end mark — see :class:`PpmdDecoder`)
    and recommended for PPMd8 whenever the container declares the member size.
    """
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: PpmdDecoder(
            order=order,
            mem_size=mem_size,
            variant=variant,
            restore_method=restore_method,
            unpack_size=unpack_size,
        ),
        codec_name="ppmd",
    )


def BcjFilterStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    decoder_attr: str,
    unpack_size: int,
    seekable: bool = False,
    collector: DiagnosticCollector | None = None,
) -> DecompressorStream:
    """Apply a ``pybcj`` BCJ branch filter (forward-only)."""
    del collector  # accepted for call-site uniformity; BCJ emits no diagnostics today
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: BcjDecoder(
            decoder_attr=decoder_attr, unpack_size=unpack_size
        ),
        codec_name="bcj",
        seekable=seekable,
    )


def Deflate64DecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
) -> DecompressorStream:
    """Decode a Deflate64 stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: Deflate64Decoder())
