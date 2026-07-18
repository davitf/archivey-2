#!/usr/bin/env python3
"""Reproducible measurements behind the performance-review findings.

Companion to ``repro.py`` (which probes the *gate*); this script produces the
numbers cited in ``budget-table.md`` and ``hotspots.md``. Requires the ``[all]``
extras (rapidgzip, py7zr). Fixture corpora are generated on first run under
``--workdir`` (default: a temp dir; pass a fixed dir to reuse across runs).

Run::

    uv run --no-sync python review/performance/measurements.py --workdir /tmp/perf-review

Sections (each prints its own table):

    budget      archivey vs stdlib: open+list / read-all / extract-all on the
                realistic-scale ZIP/TAR corpus (same generator as benchmarks/)
    peropen     per-open_archive() overhead + retained memory on a small ZIP
    solid       solid 7z: random read, selective stream/extract over-decode
    accel       rapidgzip AUTO boundary (same input just below / above the
                1 MiB compressed threshold), many-small forced-ON penalty
    rss         read(1) peak-RSS bound on a >1 MiB-compressed gzip (F3 / #128)

All wall times are medians of interleaved-ish repeat runs on a warmed cache;
ratios on shared runners are directional (see benchmarks/RESULTS.md).
"""

from __future__ import annotations

import argparse
import gc
import gzip
import logging
import random
import resource
import shutil
import statistics
import sys
import tarfile
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # repo root for `benchmarks`

from archivey import (
    AcceleratorMode,
    ArchiveyConfig,
    MemberStreams,
    open_archive,
)
from archivey.internal.measurement import enable_measurement
from benchmarks.fixtures import SCALES, build_solid_7z, build_tar, build_zip

CFG_AUTO = ArchiveyConfig()
CFG_ON = ArchiveyConfig(use_rapidgzip=AcceleratorMode.ON)
CFG_OFF = ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF)


def med(fn, n=7) -> float:
    fn()  # warm
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts) * 1000


def pair(label: str, ay, std) -> None:
    a, s = med(ay), med(std)
    print(
        f"  {label:<18} archivey {a:8.2f} ms | stdlib {s:8.2f} ms | ratio {a / s:5.2f}x"
    )


def _semi(n: int, seed: int = 7) -> bytes:
    """~2:1-compressible payload (random 4 KiB blocks, each doubled)."""
    rng = random.Random(seed)
    out = bytearray()
    while len(out) < n:
        b = rng.randbytes(4096)
        out += b + b
    return bytes(out[:n])


def _make_gz_of_compressed_size(path: Path, target: int) -> None:
    if path.exists():
        return
    raw = _semi(int(target * 1.95))
    for _ in range(6):
        c = gzip.compress(raw, 6)
        if abs(len(c) - target) / target < 0.05:
            break
        raw = _semi(int(len(raw) * target / len(c)))
    path.write_bytes(gzip.compress(raw, 6))


def section_budget(work: Path) -> None:
    print("== budget: archivey vs stdlib, realistic scale (64 x 256 KiB) ==")
    sc = SCALES["realistic"]
    zp, tp = work / "r.zip", work / "r.tar"
    if not zp.exists():
        build_zip(zp, sc)
    if not tp.exists():
        build_tar(tp, sc)

    def ay_zip_list():
        with open_archive(zp) as r:
            [m.name for m in r.members()]

    def std_zip_list():
        with zipfile.ZipFile(zp) as z:
            [z.getinfo(n) for n in z.namelist()]

    def ay_tar_list():
        with open_archive(tp) as r:
            [m.name for m in r.members()]

    def std_tar_list():
        with tarfile.open(tp) as t:
            t.getmembers()

    def ay_read(p):
        with open_archive(p) as r:
            for _m, s in r.stream_members():
                if s:
                    s.read()

    def std_zip_read():
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if not z.getinfo(n).is_dir():
                    z.read(n)

    def std_tar_read():
        with tarfile.open(tp, "r:") as t:
            for m in t.getmembers():
                if m.isfile():
                    f = t.extractfile(m)
                    if f:
                        f.read()

    def _extract(open_fn):
        d = Path(tempfile.mkdtemp(dir=work))
        try:
            open_fn(d)
        finally:
            shutil.rmtree(d)

    pair("zip open+list", ay_zip_list, std_zip_list)
    pair("tar open+list", ay_tar_list, std_tar_list)
    pair("zip read_all", lambda: ay_read(zp), std_zip_read)
    pair("tar read_all", lambda: ay_read(tp), std_tar_read)
    pair(
        "zip extract_all",
        lambda: _extract(lambda d: open_archive(zp).__enter__().extract_all(d)),
        lambda: _extract(lambda d: zipfile.ZipFile(zp).extractall(d)),
    )
    pair(
        "tar extract_all",
        lambda: _extract(lambda d: open_archive(tp).__enter__().extract_all(d)),
        lambda: _extract(lambda d: tarfile.open(tp).extractall(d, filter="data")),
    )


def section_peropen(work: Path) -> None:
    print("== per-open: small ZIP (8 x 4 KiB), open + full list ==")
    sc = SCALES["ci"]
    p = work / "small.zip"
    if not p.exists():
        build_zip(p, sc)

    def ay():
        with open_archive(p) as r:
            [m.name for m in r.members()]

    def std():
        with zipfile.ZipFile(p) as z:
            z.namelist()

    pair("open+list", ay, std)

    gc.collect()
    tracemalloc.start()
    s0 = tracemalloc.take_snapshot()
    readers = [open_archive(p) for _ in range(50)]
    for r in readers:
        list(r.members())
    s1 = tracemalloc.take_snapshot()
    total = sum(d.size_diff for d in s1.compare_to(s0, "filename"))
    tracemalloc.stop()
    for r in readers:
        r.close()
    print(f"  retained per open+listed reader: {total / 50 / 1024:.1f} KiB")


def section_solid(work: Path) -> None:
    print("== solid 7z (32 x 64 KiB, one folder): decode-bytes per access pattern ==")
    sc = SCALES["ci"]
    p = work / "solid.7z"
    if not p.exists():
        build_solid_7z(p, sc)
    unpacked = sc.solid_members * sc.solid_member_size

    def m(label, fn):
        with enable_measurement(), open_archive(p) as r:
            fn(r)
            print(
                f"  {label:<34} bytes_decompressed={r.bytes_decompressed:>10,} "
                f"({r.bytes_decompressed / unpacked:5.2f}x unpacked)"
            )

    m(
        "sequential stream_members (all)",
        lambda r: [s.read() for _mm, s in r.stream_members() if s],
    )
    m(
        "random read() reversed (all)",
        lambda r: [
            r.read(mm.name) for mm in reversed([x for x in r.members() if x.is_file])
        ],
    )
    m("read() first member only", lambda r: r.read("m000.bin"))

    def sel_first(r):
        for _mm, s in r.stream_members(lambda mm: mm.name == "m000.bin"):
            if s:
                s.read()

    m("stream_members(selector: first)", sel_first)

    def ext_first(r):
        d = Path(tempfile.mkdtemp(dir=work))
        try:
            r.extract_all(d, members=lambda mm: mm.name == "m000.bin")
        finally:
            shutil.rmtree(d)

    m("extract_all(members: first)", ext_first)

    def brk(r):
        for _mm, s in r.stream_members():
            if s:
                s.read()
            break

    m("stream_members, break after 1st", brk)


def section_accel(work: Path) -> None:
    print("== rapidgzip AUTO boundary (threshold = 1 MiB compressed) ==")
    below, above = work / "below.gz", work / "above.gz"
    _make_gz_of_compressed_size(below, 900 * 1024)
    _make_gz_of_compressed_size(above, 1152 * 1024)

    def seq(p, cfg):
        def f():
            with open_archive(
                p, config=cfg, member_streams=MemberStreams.SEEKABLE
            ) as r:
                for _m, s in r.stream_members():
                    if s:
                        s.read()

        return f

    def seek(p, cfg):
        def f():
            with open_archive(
                p, config=cfg, member_streams=MemberStreams.SEEKABLE
            ) as r:
                mm = [m for m in r.members() if m.is_file][0]
                with r.open(mm) as s:
                    sz = len(s.read())
                    s.seek(sz // 3)
                    s.read()

        return f

    for name, p in (
        (f"below ({below.stat().st_size / 2**20:.2f} MiB comp)", below),
        (f"above ({above.stat().st_size / 2**20:.2f} MiB comp)", above),
    ):
        for wl, w in (("sequential", seq), ("seek+reread", seek)):
            a = med(w(p, CFG_AUTO), n=9)
            on = med(w(p, CFG_ON), n=9)
            off = med(w(p, CFG_OFF), n=9)
            print(f"  {name} {wl:<11} AUTO {a:6.1f} | ON {on:6.1f} | OFF {off:6.1f} ms")

    manyzip = work / "many.zip"
    if not manyzip.exists():
        with zipfile.ZipFile(manyzip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i in range(1000):
                zf.writestr(f"f{i:05d}.txt", (f"line {i} " * 600)[:4096])

    def rd(cfg, ms):
        def f():
            with open_archive(manyzip, config=cfg, member_streams=ms) as r:
                for _m, s in r.stream_members():
                    if s:
                        s.read()

        return f

    auto = med(rd(CFG_AUTO, MemberStreams.SEEKABLE), n=5)
    on = med(rd(CFG_ON, MemberStreams.SEEKABLE), n=5)
    print(
        f"  many-small ZIP (1000 x 4 KiB): AUTO {auto:.0f} ms | forced ON {on:.0f} ms "
        f"({on / auto:.1f}x penalty AUTO avoids)"
    )


def section_rss(work: Path) -> None:
    print("== read(1) peak-RSS bound on accelerated big gzip (F3 / #128) ==")
    big = work / "big-accel.gz"
    _make_gz_of_compressed_size(big, 8 * 1024 * 1024)

    def rss():
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    base = rss()
    with open_archive(big, member_streams=MemberStreams.SEEKABLE) as r:
        mm = [m for m in r.members() if m.is_file][0]
        with r.open(mm) as s:
            s.read(1)
            print(
                f"  peak RSS delta after read(1): {rss() - base:.0f} MiB "
                f"(compressed {big.stat().st_size / 2**20:.1f} MiB, ~2x that uncompressed)"
            )


SECTIONS = {
    "budget": section_budget,
    "peropen": section_peropen,
    "solid": section_solid,
    "accel": section_accel,
    "rss": section_rss,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sections", nargs="*", default=[], help=f"subset of {sorted(SECTIONS)}"
    )
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    work = args.workdir or Path(tempfile.mkdtemp(prefix="perf-review-"))
    work.mkdir(parents=True, exist_ok=True)
    for name in args.sections or sorted(SECTIONS):
        SECTIONS[name](work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
