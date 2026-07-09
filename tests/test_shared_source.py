"""Unit tests for ``SharedSource`` (``streams/streamtools/shared.py``).

The primitive is the concurrent-open foundation for Phase 6 native readers, so it gets
focused coverage (per CONTRIBUTING's narrow exception for stream primitives): interleaved
single-thread reads, cross-thread data-correctness under the lock, and stdlib-shaped
misuse errors.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest

from archivey.internal.streams.streamtools import SharedSource

DATA = b"0123456789abcdefghijklmnopqrstuvwxyz"


class TestSharedSourceInterleaved:
    def test_adjacent_views_interleaved_partial_reads(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        left = shared.view(0, 10)  # "0123456789"
        right = shared.view(10, 10)  # "abcdefghij"

        # Shuffled partial-read interleaving: neither view may see the other's bytes.
        assert left.read(3) == b"012"
        assert right.read(4) == b"abcd"
        assert left.read(2) == b"34"
        assert right.read(3) == b"efg"
        assert left.read() == b"56789"
        assert right.read() == b"hij"
        assert left.read(1) == b""
        assert right.read(1) == b""

    def test_overlapping_views_interleaved(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        a = shared.view(5, 10)  # DATA[5:15]
        b = shared.view(8, 10)  # DATA[8:18]

        assert a.read(4) == DATA[5:9]
        assert b.read(4) == DATA[8:12]
        assert a.read() == DATA[9:15]
        assert b.read() == DATA[12:18]

    @pytest.mark.parametrize(
        ("regions", "chunk"),
        [
            (((0, 8), (8, 8), (16, 8)), 3),
            (((0, 10), (20, 10)), 1),
            (((5, 5), (15, 5), (25, 5)), 2),
        ],
    )
    def test_parametrized_non_overlapping_interleave(
        self,
        regions: tuple[tuple[int, int], ...],
        chunk: int,
    ) -> None:
        # Plain parametrized stand-in for a Hypothesis property (hypothesis-property-tests
        # has not landed); random non-overlapping regions × chunked interleaving.
        shared = SharedSource(io.BytesIO(DATA))
        views = [shared.view(start, length) for start, length in regions]
        expected = [DATA[start : start + length] for start, length in regions]
        got = [bytearray() for _ in regions]

        active = list(range(len(views)))
        while active:
            for i in list(active):
                piece = views[i].read(chunk)
                if not piece:
                    active.remove(i)
                else:
                    got[i].extend(piece)

        assert [bytes(g) for g in got] == expected


class TestSharedSourceThreads:
    def test_two_threads_distinct_views_byte_exact(self) -> None:
        # Deterministic: each thread reads its whole view in one go under the lock;
        # assert byte-exact output (data-correct, not parallel).
        payload = bytes(range(256)) * 64  # 16 KiB
        shared = SharedSource(io.BytesIO(payload))
        mid = len(payload) // 2
        left_view = shared.view(0, mid)
        right_view = shared.view(mid, len(payload) - mid)

        results: list[bytes | None] = [None, None]
        errors: list[BaseException] = []

        def worker(index: int, view: object, expected: bytes) -> None:
            try:
                # Many small reads to exercise lock contention without flakiness.
                chunks = bytearray()
                while True:
                    piece = view.read(17)  # type: ignore[attr-defined]
                    if not piece:
                        break
                    chunks.extend(piece)
                results[index] = bytes(chunks)
                assert results[index] == expected
            except BaseException as exc:  # noqa: BLE001 — collect for the main thread
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(0, left_view, payload[:mid]))
        t2 = threading.Thread(target=worker, args=(1, right_view, payload[mid:]))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []
        assert results[0] == payload[:mid]
        assert results[1] == payload[mid:]


class TestSharedSourceMisuse:
    def test_read_after_view_close_raises(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        view = shared.view(0, 10)
        view.close()
        with pytest.raises(ValueError, match="closed"):
            view.read(1)

    def test_read_after_source_close_raises(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        view = shared.view(0, 10)
        shared.close()
        with pytest.raises(ValueError, match="closed"):
            view.read(1)

    def test_seek_after_close_raises(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        view = shared.view(0, 10)
        view.close()
        with pytest.raises(ValueError, match="closed"):
            view.seek(0)

    def test_out_of_bounds_view_raises_at_construction(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        with pytest.raises(ValueError, match="exceeds source size"):
            shared.view(0, len(DATA) + 1)
        with pytest.raises(ValueError, match="past the end"):
            shared.view(len(DATA) + 5, 1)

    def test_view_does_not_close_source(self) -> None:
        buf = io.BytesIO(DATA)
        shared = SharedSource(buf)
        view = shared.view(0, 5)
        view.close()
        # Source still usable; a fresh view still reads.
        assert shared.view(0, 5).read() == DATA[:5]
        assert not buf.closed

    def test_path_source_owns_and_closes_handle(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(DATA)
        shared = SharedSource(path)
        assert shared.view(10, 5).read() == DATA[10:15]
        shared.close()
        with pytest.raises(ValueError, match="closed"):
            shared.view(0, 1)

    def test_relative_seek_underflow_clamps_like_bytesio(self) -> None:
        # BytesIO clamps a SEEK_CUR/SEEK_END result past the origin to 0 (only a negative
        # SEEK_SET raises); backwards-from-end probes on a short source (e.g. ZipFile's
        # EOCD probe) rely on the clamp rather than a raw ValueError.
        shared = SharedSource(io.BytesIO(DATA[:10]))
        view = shared.view(0)
        assert view.seek(-100, io.SEEK_END) == 0
        assert view.tell() == 0
        view.seek(3)
        assert view.seek(-100, io.SEEK_CUR) == 0
        assert view.tell() == 0

    def test_seek_set_negative_still_raises(self) -> None:
        shared = SharedSource(io.BytesIO(DATA))
        view = shared.view(0, 10)
        with pytest.raises(ValueError, match="Negative seek position"):
            view.seek(-1)
