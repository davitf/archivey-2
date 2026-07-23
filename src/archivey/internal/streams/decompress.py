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

# Per-call ceiling for extra-NUL recovery and post-NUL empty drains. A single
# ``decode(b"\0", large_remaining)`` on truncated mid-stream input is the
# exit-after-green / mid-suite abort on pyppmd 1.3.x; chunking at 64 avoids that
# call shape. When the container pack is known-complete, empty ``decode(b"", 64)``
# drains may still finish a large tail (including past premature ``eof``); when the
# pack is known-incomplete, we refuse those post-eof drains (near-EOF MemoryError
# otherwise). See ``docs/internal/ppmd-exit-after-green-exploration.md``.
_PPMD_EXTRA_NUL_MAX_OUTPUT = 64


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

    ``pack_size`` is the container-declared compressed length (7z pack stream / ZIP
    compressed size, or the sized view length). When set, empty post-``eof`` drains
    that chase ``unpack_size`` are allowed only after ``fed_compressed >= pack_size``;
    a short pack delivery stops instead (avoids near-EOF ``MemoryError`` on
    truncated members). It is **required for PPMd7** (see ``__init__``): without it a
    premature native ``eof`` cannot be told from truncation, and the only safe choice
    left is to refuse the drain, which silently truncates valid members on chunked
    reads. ``PpmdCodec`` fills it from the sized source (``compressed_input_size``) or,
    for chained 7z folders whose PPMd input is unsized (AES), from the plumbed coder
    input size. PPMd8 may leave it unknown; recovery is then conservative — at most one
    capped NUL and **no** chunked empty drains. At compressed EOF, at most one
    documented extra NUL is injected with a per-call budget of
    :data:`_PPMD_EXTRA_NUL_MAX_OUTPUT`; unsized PPMd8 gets **no** post-eof drain at all
    (its end mark terminates valid decodes; a drain would only fabricate trailing bytes).

    **Invariant:** ``pack_size`` must measure the same byte stream that
    ``feed()`` accumulates into ``_fed_compressed`` (the PPMd coder's compressed
    input — after ZIP method-98 header stripping / as the 7z pack ``SlicingStream``
    length). If a wrapper sets ``compressed_input_size`` to a larger enclosing
    member while feeding only a subset, the gate would wrongly treat a complete
    pack as short and suppress legitimate post-eof drains.
    """

    def __init__(
        self,
        *,
        order: int,
        mem_size: int,
        variant: int = 7,
        restore_method: int = 0,
        unpack_size: int | None = None,
        pack_size: int | None = None,
    ) -> None:
        import pyppmd

        if variant != 8 and unpack_size is None:
            raise ValueError(
                "PPMd7 (7z var.H) requires unpack_size: the format has no end mark, "
                "and decoding without the exact output bound runs pyppmd past the "
                "end of stream (native heap corruption on 1.3.x — see "
                "docs/internal/known-issues.md)"
            )
        if variant != 8 and pack_size is None:
            # Without pack_size, a premature native ``eof`` (pyppmd flips it early on a
            # small ``max_length`` over compressible data) is indistinguishable from
            # truncation: draining toward unpack_size to finish the tail can MemoryError
            # on 1.3.x, so the decoder must refuse it — which silently truncates a valid
            # member on chunked reads. PPMd7 is 7z-only and 7z always knows the pack
            # length (sized pack slice, or the preceding coder's output for AES folders),
            # so require it rather than choose between truncation and a crash.
            raise ValueError(
                "PPMd7 (7z var.H) requires pack_size: it has no end mark, so completing "
                "a member past a premature native eof needs the declared compressed "
                "length to tell full delivery from truncation (see "
                "docs/internal/known-issues.md)"
            )
        self._order = order
        self._mem_size = mem_size
        self._variant = variant
        self._restore_method = restore_method
        self._unpack_size = unpack_size
        self._pack_size = pack_size
        self._produced = 0
        self._fed_compressed = 0
        self._nul_injected = False
        self._compressed_eof = False
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
            pack_size=self._pack_size,
        )

    def _max_length(self) -> int:
        if self._unpack_size is None:
            return -1
        return max(0, self._unpack_size - self._produced)

    def _pack_complete(self) -> bool | None:
        """True if declared pack fully fed, False if known short, None if unknown."""
        if self._pack_size is None:
            return None
        return self._fed_compressed >= self._pack_size

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

    def _nul_budget(self, max_length: int) -> int:
        budget = _PPMD_EXTRA_NUL_MAX_OUTPUT
        if max_length >= 0:
            budget = min(max_length, budget)
        return budget

    def _inject_nul_once(self, max_length: int) -> bytes:
        """Documented single extra NUL (pyppmd / py7zr); never loop fabricated input."""
        if self._nul_injected or self._decomp.eof:
            return b""
        if not getattr(self._decomp, "needs_input", False):
            return b""
        self._nul_injected = True
        return self._decomp.decode(b"\0", self._nul_budget(max_length))

    def _drain_empty_chunked(self, max_length: int) -> bytes:
        """Pull remaining output in ``_PPMD_EXTRA_NUL_MAX_OUTPUT`` empty decodes.

        Only for a **known-complete** pack (see :meth:`flush`): premature native
        ``eof`` after a small ``max_length`` can still leave legitimate symbols
        reachable via ``decode(b"", …)`` — so this intentionally continues past
        native ``eof`` (breaking on eof would defeat premature-eof recovery).
        Stops on ``needs_input``, quiet empty, or budget exhaustion. Does **not**
        run when ``pack_size`` is unknown or short. Corrupt-but-declared-complete
        packs can still fill toward ``unpack_size`` here; container CRC is the
        backstop. Worst-case iteration count is ``remaining + 2`` at 64 bytes
        per call (perf cliff on huge members if this path is hit).
        """
        if max_length == 0:
            return b""
        parts: list[bytes] = []
        remaining = max_length
        quiet = 0
        # Bound iterations: worst case one byte per call up to remaining.
        for _ in range(remaining + 2):
            if remaining <= 0:
                break
            if getattr(self._decomp, "needs_input", False):
                break
            budget = min(_PPMD_EXTRA_NUL_MAX_OUTPUT, remaining)
            chunk = self._decomp.decode(b"", budget)
            if not chunk:
                quiet += 1
                if quiet >= 2:
                    break
                continue
            quiet = 0
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _decode(self, data: bytes, max_length: int) -> bytes:
        if max_length == 0:
            return b""
        # Decoding after native EOF is trailing garbage at best (and the crashy
        # runaway path on pyppmd 1.3.x when unbounded) — drop the input instead.
        if self._decomp.eof and max_length < 0:
            return b""
        # Empty + needs_input after compressed EOF: at most one documented NUL.
        # Before compressed EOF, empty+needs_input means "read more pack bytes" —
        # do not fabricate input.
        if (
            not data
            and getattr(self._decomp, "needs_input", False)
            and not self._decomp.eof
        ):
            if not self._compressed_eof:
                return b""
            return self._inject_nul_once(max_length)
        if max_length < 0:
            return self._decode_unsized(data)
        return self._decomp.decode(data, max_length)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if chunk:
            self._fed_compressed += len(chunk)
        # Honour both the container unpack_size cap and the stream-layer read budget.
        unpack_cap = self._max_length()
        if max_length >= 0 and unpack_cap >= 0:
            limit = min(max_length, unpack_cap)
        elif max_length >= 0:
            limit = max_length
        else:
            limit = unpack_cap
        # Empty drains past native eof are only safe when the declared pack was
        # fully delivered. Unknown or short pack: refuse (near-EOF MemoryError /
        # garbage fill). Callers must pass pack_size (or sized-view
        # compressed_input_size) for correct premature-eof completion.
        if not chunk and self._pack_complete() is not True and self._decomp.eof:
            return DecodeOut(b"")
        out = self._decode(chunk, limit)
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        # Compressed EOF: optionally one documented extra NUL, then (only when the
        # pack is known-complete) chunked empty drains. Never inject fabricated
        # NULs in a loop. Unknown pack_size is treated like incomplete for drains:
        # single capped NUL only — do not chase unpack_size.
        self._compressed_eof = True
        max_length = self._max_length()
        if max_length == 0:
            return DecodeOut(b"")
        out = b""
        if not self._decomp.eof and getattr(self._decomp, "needs_input", False):
            more = self._inject_nul_once(max_length)
            out += more
            self._produced += len(more)
            max_length = self._max_length()
        # Empty drains past a premature native ``eof`` only run when the pack is
        # known-complete AND a container ``unpack_size`` bounds them (``max_length >= 0``).
        # Without that bound the drain is pure fabrication: an unsized PPMd8 stream ends
        # at its end mark, so any post-eof pull is trailing garbage (measured: +N bytes
        # on compressible payloads). Corrupt-but-declared-complete sized packs can still
        # fill toward ``unpack_size`` here — container CRC is the backstop.
        if max_length > 0 and self._pack_complete() is True:
            if not getattr(self._decomp, "needs_input", False):
                drained = self._drain_empty_chunked(max_length)
                out += drained
                self._produced += len(drained)
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
    pack_size: int | None = None,
) -> DecompressorStream:
    """Decode a PPMd stream (forward-only).

    ``variant=7`` is 7z PPMd var.H; ``variant=8`` is ZIP method 98 (PPMd8).
    ``unpack_size`` is required for PPMd7 (no end mark — see :class:`PpmdDecoder`)
    and recommended for PPMd8 whenever the container declares the member size.
    ``pack_size`` is the container-declared compressed length (or sized view);
    when set, post-eof empty drains are gated on full pack delivery.
    """
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: PpmdDecoder(
            order=order,
            mem_size=mem_size,
            variant=variant,
            restore_method=restore_method,
            unpack_size=unpack_size,
            pack_size=pack_size,
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
