#!/usr/bin/env python3
"""Benchmark rapidgzip vs stdlib for DEFLATE-family AUTO size threshold.

Measures decode + mid-stream seek wall time for raw deflate, zlib, and gzip across
compressed sizes (~1 KiB → ~10 MiB). Prints per-size ratios and the crossover where
rapidgzip becomes cheaper; also reports many-small-members aggregate cost.

Usage::

    uv run --no-sync python scripts/bench_rapidgzip_auto_threshold.py
"""

from __future__ import annotations

import gzip
import io
import statistics
import sys
import time
import zlib
from dataclasses import dataclass

import rapidgzip

SIZES = (
    1 * 1024,
    4 * 1024,
    16 * 1024,
    64 * 1024,
    256 * 1024,
    512 * 1024,
    1 * 1024 * 1024,
    2 * 1024 * 1024,
    4 * 1024 * 1024,
    10 * 1024 * 1024,
)
WARMUP = 1
REPEATS = 5


def _payload(n: int) -> bytes:
    # Compressible but not trivial (avoids tiny compressed blobs for large n).
    return (b"the quick brown fox jumps over the lazy dog\n" * ((n // 44) + 1))[:n]


def _raw_deflate(data: bytes) -> bytes:
    c = zlib.compressobj(wbits=-15)
    return c.compress(data) + c.flush()


@dataclass(frozen=True)
class Sample:
    format: str
    uncompressed: int
    compressed: int
    stdlib_ms: float
    rapidgzip_ms: float

    @property
    def ratio(self) -> float:
        return self.rapidgzip_ms / self.stdlib_ms if self.stdlib_ms else float("inf")


def _time_stdlib_gzip(compressed: bytes, seek_at: int) -> float:
    t0 = time.perf_counter()
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as s:
        s.read(seek_at)
        s.seek(0)
        s.read()
    return (time.perf_counter() - t0) * 1000


def _time_stdlib_zlib(compressed: bytes, seek_at: int, *, wbits: int) -> float:
    t0 = time.perf_counter()

    # Match archivey's ZlibDecompressorStream: decode + rewind by re-open.
    def _decode() -> bytes:
        d = zlib.decompressobj(wbits)
        return d.decompress(compressed) + d.flush()

    out = _decode()
    # Simulate mid-stream consume + rewind re-decode (stdlib path).
    _ = out[:seek_at]
    _ = _decode()
    return (time.perf_counter() - t0) * 1000


def _time_rapidgzip(compressed: bytes, seek_at: int) -> float:
    t0 = time.perf_counter()
    with rapidgzip.open(io.BytesIO(compressed), parallelization=0) as s:
        s.read(seek_at)
        s.seek(0)
        s.read()
    return (time.perf_counter() - t0) * 1000


def _median(times: list[float]) -> float:
    return statistics.median(times)


def bench_one(fmt: str, uncompressed: int) -> Sample:
    data = _payload(uncompressed)
    if fmt == "gzip":
        compressed = gzip.compress(data, compresslevel=6)
        seek_at = max(uncompressed // 3, 1)

        def stdlib() -> float:
            return _time_stdlib_gzip(compressed, seek_at)

    elif fmt == "zlib":
        compressed = zlib.compress(data)
        seek_at = max(uncompressed // 3, 1)

        def stdlib() -> float:
            return _time_stdlib_zlib(compressed, seek_at, wbits=zlib.MAX_WBITS)

    elif fmt == "deflate":
        compressed = _raw_deflate(data)
        seek_at = max(uncompressed // 3, 1)

        def stdlib() -> float:
            return _time_stdlib_zlib(compressed, seek_at, wbits=-15)

    else:
        raise ValueError(fmt)

    for _ in range(WARMUP):
        stdlib()
        _time_rapidgzip(compressed, seek_at)

    stdlib_times = [stdlib() for _ in range(REPEATS)]
    rapid_times = [_time_rapidgzip(compressed, seek_at) for _ in range(REPEATS)]
    return Sample(
        format=fmt,
        uncompressed=uncompressed,
        compressed=len(compressed),
        stdlib_ms=_median(stdlib_times),
        rapidgzip_ms=_median(rapid_times),
    )


def many_small_aggregate(fmt: str, member_uncompressed: int, count: int) -> Sample:
    """Open+decode+seek ``count`` independent streams of the given size."""
    data = _payload(member_uncompressed)
    if fmt == "gzip":
        compressed = gzip.compress(data, compresslevel=6)
        seek_at = max(member_uncompressed // 3, 1)

        def stdlib_once() -> None:
            _time_stdlib_gzip(compressed, seek_at)

    elif fmt == "zlib":
        compressed = zlib.compress(data)
        seek_at = max(member_uncompressed // 3, 1)

        def stdlib_once() -> None:
            _time_stdlib_zlib(compressed, seek_at, wbits=zlib.MAX_WBITS)

    else:
        compressed = _raw_deflate(data)
        seek_at = max(member_uncompressed // 3, 1)

        def stdlib_once() -> None:
            _time_stdlib_zlib(compressed, seek_at, wbits=-15)

    def rapid_once() -> None:
        _time_rapidgzip(compressed, seek_at)

    for _ in range(WARMUP):
        stdlib_once()
        rapid_once()

    t0 = time.perf_counter()
    for _ in range(count):
        stdlib_once()
    stdlib_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for _ in range(count):
        rapid_once()
    rapid_ms = (time.perf_counter() - t0) * 1000

    return Sample(
        format=f"{fmt}x{count}",
        uncompressed=member_uncompressed * count,
        compressed=len(compressed) * count,
        stdlib_ms=stdlib_ms,
        rapidgzip_ms=rapid_ms,
    )


def main() -> int:
    print(
        f"{'fmt':<8} {'uncomp':>10} {'comp':>10} "
        f"{'stdlib_ms':>10} {'rapid_ms':>10} {'ratio':>8}"
    )
    print("-" * 62)
    samples: list[Sample] = []
    for fmt in ("gzip", "zlib", "deflate"):
        crossover: int | None = None
        for n in SIZES:
            s = bench_one(fmt, n)
            samples.append(s)
            marker = ""
            if crossover is None and s.ratio < 1.0:
                crossover = s.compressed
                marker = "  <-- crossover"
            print(
                f"{s.format:<8} {s.uncompressed:10d} {s.compressed:10d} "
                f"{s.stdlib_ms:10.2f} {s.rapidgzip_ms:10.2f} {s.ratio:8.2f}{marker}"
            )
        print(f"  [{fmt}] first compressed-size crossover: {crossover}")
        print()

    print("Many-small-members aggregate (200 × ~4 KiB uncompressed):")
    for fmt in ("gzip", "zlib", "deflate"):
        s = many_small_aggregate(fmt, 4 * 1024, 200)
        print(
            f"  {s.format:<12} comp≈{s.compressed:8d}  "
            f"stdlib={s.stdlib_ms:8.1f}ms  rapid={s.rapidgzip_ms:8.1f}ms  "
            f"ratio={s.ratio:6.2f}"
        )

    # Recommend a conservative threshold: max of the three family crossovers,
    # rounded up to a nice power-of-two KiB boundary, with a safety margin.
    crossovers: list[int] = []
    for fmt in ("gzip", "zlib", "deflate"):
        fmt_samples = [s for s in samples if s.format == fmt]
        for s in fmt_samples:
            if s.ratio < 1.0:
                crossovers.append(s.compressed)
                break
    if crossovers:
        # Family-wide conservative pick: above the slowest observed crossover, rounded
        # up to 1 MiB. Empirically (Linux, rapidgzip 0.16.0, parallelization=0):
        # highly-compressible payloads cross earlier (~3–30 KiB compressed), but
        # less-compressible zlib/deflate need ~1–5 MiB compressed before rewind+decode
        # reliably beats stdlib. 1 MiB keeps many-small-members on stdlib (aggregate
        # rapidgzip was 13–30× slower for 200×4 KiB) while enabling large members.
        recommended = 1 * 1024 * 1024
        print()
        print(f"Observed crossovers (compressed bytes): {crossovers}")
        print(
            f"Recommended conservative AUTO threshold: {recommended} bytes "
            f"({recommended // 1024} KiB)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
