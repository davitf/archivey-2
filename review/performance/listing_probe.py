"""Listing decomposition probes (listing-attribution.md).

Sections:

    zip      2000x64B STORED ZIP: zipfile vs archivey, ablated into
             open-only / open+derivation / full members()
    member   ArchiveMember construction micro-bench (minimal / full kwargs)
    sevenzip 2000-member 7z vs py7zr.list, plus a name-read call census
    rar      committed 6-member fixture vs rarfile (fixed-cost artifact demo)

Usage: python review/performance/listing_probe.py <section> [workdir]

Warm, in-process medians; compare only numbers from one process (see
residual-gap.md methodology). Accept criteria for the L1/L2 worklist items in
listing-attribution.md are defined against these probes.
"""

from __future__ import annotations

import io
import statistics
import sys
import time
import zipfile
from pathlib import Path

from archivey import open_archive

MEMBERS = 2000


def bench(fn, rounds: int = 15) -> float:
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


def _zip_fixture(work: Path) -> Path:
    zp = work / "listing-many.zip"
    if not zp.exists():
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_STORED) as zf:
            for i in range(MEMBERS):
                zf.writestr(f"dir{i % 20}/file{i:04d}.txt", b"x" * 64)
    return zp


def section_zip(work: Path) -> None:
    zp = _zip_fixture(work)

    def std() -> None:
        with zipfile.ZipFile(zp) as zf:
            zf.infolist()

    def open_only() -> None:
        with open_archive(zp):
            pass

    def derive_only() -> None:
        # zipfile parse + _to_member derivation; no registration/accounting/index.
        with open_archive(zp) as ar:
            for info in ar._archive.infolist():
                ar._to_member(info)

    def full() -> None:
        with open_archive(zp) as ar:
            _ = ar.info
            list(ar.members())

    std(), open_only(), derive_only(), full()
    s, o, d, f = bench(std), bench(open_only), bench(derive_only), bench(full)
    n = MEMBERS
    print(f"zipfile infolist   {s:6.2f} ms")
    print(f"open_archive only  {o:6.2f} ms   (fixed open cost {o - s:.2f} ms)")
    print(f"open + derivation  {d:6.2f} ms   ({(d - o) / n * 1000:.2f} us/member)")
    print(
        f"full members()     {f:6.2f} ms   (register/account/index {(f - d) / n * 1000:.2f} us/member)"
    )
    print(f"ratio {f / s:.2f}x   total overhead {(f - s) / n * 1000:.2f} us/member")


def section_member(work: Path) -> None:
    del work
    from archivey.types import (
        ArchiveMember,
        CompressionAlgorithm,
        CompressionMethod,
        CreateSystem,
        MemberType,
    )

    comp = (CompressionMethod(algo=CompressionAlgorithm.STORED),)
    n = 20000

    def minimal() -> None:
        for _ in range(n):
            ArchiveMember(type=MemberType.FILE, name="a.txt")

    def full_kwargs() -> None:
        # Mirrors how zip_reader._to_member calls it (explicit Nones included).
        for _ in range(n):
            ArchiveMember(
                type=MemberType.FILE,
                name="dir/file.txt",
                raw_name=b"dir/file.txt",
                size=64,
                compressed_size=64,
                modified=None,
                accessed=None,
                created=None,
                mode=0o644,
                uid=None,
                gid=None,
                uname=None,
                gname=None,
                link_target=None,
                compression=comp,
                is_encrypted=False,
                comment=None,
                create_system=CreateSystem.UNIX,
                windows_attrs=None,
                hashes={"crc32": 12345},
                extra={},
            )

    def zipinfo() -> None:
        for _ in range(n):
            zipfile.ZipInfo("dir/file.txt")

    minimal(), full_kwargs(), zipinfo()
    for label, fn in (
        ("minimal args", minimal),
        ("~20 kwargs  ", full_kwargs),
        ("ZipInfo ref ", zipinfo),
    ):
        per = bench(fn, rounds=9) / n * 1000
        print(f"ArchiveMember {label}: {per:.2f} us/object")
    m = ArchiveMember(type=MemberType.FILE, name="a.txt")
    # slots=True: no __dict__; report object size only.
    print(f"slots object {sys.getsizeof(m)} B (no __dict__)")


def section_sevenzip(work: Path) -> None:
    import py7zr

    from archivey.internal.streams.streamtools import binaryio

    sz = work / "listing-many.7z"
    if not sz.exists():
        with py7zr.SevenZipFile(sz, "w") as zf:
            for i in range(MEMBERS):
                zf.writef(io.BytesIO(b"x" * 64), f"dir{i % 20}/file{i:04d}.txt")

    def ay() -> None:
        with open_archive(sz) as ar:
            _ = ar.info
            list(ar.members())

    def peer() -> None:
        with py7zr.SevenZipFile(sz, "r") as a:
            list(a.list())

    ay(), peer()
    a, p = bench(ay, rounds=9), bench(peer, rounds=9)
    print(
        f"7z {MEMBERS} members: archivey {a:.2f} ms   py7zr.list {p:.2f} ms   ratio {a / p:.2f}x"
    )

    # Census: how many read_exact calls does one listing make? (L1 target: a small
    # constant per member, not O(name-length). Pre-fix: ~22.5/member here.)
    calls = 0
    real = binaryio.read_exact

    def counting(stream, length):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real(stream, length)

    binaryio.read_exact = counting
    try:
        # The parser imports read_exact via the streamtools package; patch both.
        from archivey.internal.backends import sevenzip_parser

        parser_real = sevenzip_parser.read_exact
        sevenzip_parser.read_exact = counting
        try:
            ay()
        finally:
            sevenzip_parser.read_exact = parser_real
    finally:
        binaryio.read_exact = real
    print(f"read_exact calls per listing: {calls}  ({calls / MEMBERS:.1f}/member)")


def section_rar(work: Path) -> None:
    del work
    import rarfile

    fx = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "rar"
        / "basic_solid__.rar"
    )

    def ay() -> None:
        with open_archive(fx) as ar:
            _ = ar.info
            list(ar.members())

    def peer() -> None:
        with rarfile.RarFile(str(fx)) as a:
            a.infolist()

    ay(), peer()
    a, p = bench(ay, rounds=25), bench(peer, rounds=25)
    with open_archive(fx) as ar:
        n = len(list(ar.members()))
    print(f"RAR fixture ({n} members, {fx.stat().st_size} B):")
    print(f"  archivey {a:.3f} ms   rarfile {p:.3f} ms   ratio {a / p:.2f}x")
    print("  NOTE: fixed-cost artifact — too few members to measure per-member parse")


def main() -> None:
    section = sys.argv[1] if len(sys.argv) > 1 else "zip"
    work = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/archivey-listing-probe")
    )
    work.mkdir(parents=True, exist_ok=True)
    {
        "zip": section_zip,
        "member": section_member,
        "sevenzip": section_sevenzip,
        "rar": section_rar,
    }[section](work)


if __name__ == "__main__":
    main()
