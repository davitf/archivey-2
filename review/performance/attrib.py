"""Post-#136/#137 attribution of the residual ZIP read-all gap (residual-gap.md).

Runs against the installed/`PYTHONPATH` archivey tree. Sections:

    bench    component parity: zipfile vs manual-floor vs archivey read-all
    census   OS-level read() call counts, archivey vs zipfile
    sweep    warm, single-process `_COMPRESSED_READ_SIZE` sweep (the decode-
             granularity lever), mirrored order to expose ordering artifacts
    profile  paired cProfile at the plateau feed size

Usage: python review/performance/attrib.py <section> [workdir]

Fixture: 64 members x 256 KiB of ~2:1-compressible payload, DEFLATE. All wall
numbers are medians of 21 in-process rounds after a warm-up pass; compare only
numbers produced inside one process (see residual-gap.md "methodology").
"""

from __future__ import annotations

import cProfile
import io
import pstats
import random
import statistics
import sys
import time
import zipfile
import zlib
from pathlib import Path

from archivey import open_archive

ROUNDS = 21
MEMBERS = 64
MEMBER_SIZE = 262144


def _semi(rng: random.Random, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        block = rng.randbytes(512)
        out += block + block  # ~2:1 under DEFLATE
    return bytes(out[:n])


def build_fixture(work: Path) -> Path:
    zp = work / "attrib.zip"
    if not zp.exists():
        rng = random.Random(42)
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i in range(MEMBERS):
                zf.writestr(f"f{i:03d}.bin", _semi(rng, MEMBER_SIZE))
    return zp


def bench(fn) -> float:
    times = []
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


def t_zipfile(zp: Path) -> None:
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            with zf.open(info) as f:
                f.read()


def t_floor(zp: Path) -> None:
    """Manual floor: seek + read the compressed extent + one C decompress call."""
    with open(zp, "rb") as fh, zipfile.ZipFile(fh) as zf:
        for info in zf.infolist():
            fh.seek(info.header_offset)
            hdr = fh.read(30)
            n_name = int.from_bytes(hdr[26:28], "little")
            n_extra = int.from_bytes(hdr[28:30], "little")
            fh.seek(info.header_offset + 30 + n_name + n_extra)
            comp = fh.read(info.compress_size)
            out = zlib.decompressobj(-15).decompress(comp)
            assert len(out) == info.file_size


def t_archivey(zp: Path) -> None:
    with open_archive(zp) as ar:
        for _member, stream in ar.stream_members():
            if stream is not None:
                stream.read()


def section_bench(zp: Path) -> None:
    t_zipfile(zp)
    t_archivey(zp)  # warm both paths before any timed round
    base = bench(lambda: t_zipfile(zp))
    floor = bench(lambda: t_floor(zp))
    ours = bench(lambda: t_archivey(zp))
    print(f"zipfile   {base:8.2f} ms")
    print(f"floor     {floor:8.2f} ms")
    print(f"archivey  {ours:8.2f} ms   ratio={ours / base:.2f}x")


def section_census(zp: Path) -> None:
    class Counting(io.BufferedReader):
        reads = 0

        def read(self, n: int = -1) -> bytes:
            Counting.reads += 1
            return super().read(n)

    Counting.reads = 0
    with open_archive(Counting(io.FileIO(zp))) as ar:
        for _member, stream in ar.stream_members():
            if stream is not None:
                stream.read()
    ours = Counting.reads

    Counting.reads = 0
    with zipfile.ZipFile(Counting(io.FileIO(zp))) as zf:
        for info in zf.infolist():
            with zf.open(info) as f:
                f.read()
    print(f"os-level read() calls: archivey={ours}  zipfile={Counting.reads}")


def section_sweep(zp: Path) -> None:
    from archivey.internal.streams import decompressor_stream as ds

    t_zipfile(zp)
    t_archivey(zp)
    default = ds._COMPRESSED_READ_SIZE
    # Mirrored order: if the two runs of one size disagree, the process is not
    # thermally/JIT-stable and the whole sweep should be rerun.
    for size in (1 << 20, 262144, 65536, 8192, 65536, 262144, 1 << 20):
        ds._COMPRESSED_READ_SIZE = size
        print(f"feed={size:>8}  {bench(lambda: t_archivey(zp)):8.2f} ms")
    ds._COMPRESSED_READ_SIZE = default
    print(f"zipfile       {bench(lambda: t_zipfile(zp)):8.2f} ms")


def section_profile(zp: Path) -> None:
    from archivey.internal.streams import decompressor_stream as ds

    ds._COMPRESSED_READ_SIZE = 1 << 20  # plateau: ~single-shot decompress
    t_archivey(zp)
    for label, fn in (("archivey", t_archivey), ("zipfile", t_zipfile)):
        pr = cProfile.Profile()
        pr.enable()
        for _ in range(10):
            fn(zp)
        pr.disable()
        print(f"\n--- {label} tottime top 15 (10 rounds) ---")
        pstats.Stats(pr).sort_stats("tottime").print_stats(15)


def main() -> None:
    section = sys.argv[1] if len(sys.argv) > 1 else "bench"
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/archivey-attrib")
    work.mkdir(parents=True, exist_ok=True)
    zp = build_fixture(work)
    {
        "bench": section_bench,
        "census": section_census,
        "sweep": section_sweep,
        "profile": section_profile,
    }[section](zp)


if __name__ == "__main__":
    main()
