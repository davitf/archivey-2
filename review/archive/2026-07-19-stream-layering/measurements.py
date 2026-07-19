#!/usr/bin/env python3
"""Runnable evidence for the stream-layering review.

Designed against the post-#136 stack (nested outer ``ArchiveStream`` collapsed;
codec ``ArchiveStream`` may still sit under ``VerifyingStream``). On plain
``main`` before #136 lands, ``measurements.py stack`` shows an extra outer nest
on ``stream_members`` only; ``open()`` already looks like the post-collapse
shape. Synthetic fusion sections are valid either way.

Run::

    uv run --no-sync python review/stream-layering/measurements.py
    uv run --no-sync python review/stream-layering/measurements.py f1 f2 stack stored

Sections:

    f1       VerifyingStream.read(0) false-EOF (F1)
    f2       close-leak on typed probe error (F2)
    stack    live STORED / DEFLATE handle dump after first read
    stored   STORED ZIP isolation + synthetic fusion stacks
    all      everything (default)
"""

from __future__ import annotations

import argparse
import io
import os
import statistics
import struct
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

# Prefer the checkout's src/ over an editable install of another revision so
# `python review/stream-layering/measurements.py` reflects the tree you are in
# (important when comparing main vs the #136 collapse).
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from archivey import open_archive  # noqa: E402
from archivey.exceptions import (  # noqa: E402
    CorruptionError,
    EncryptionError,
    TruncatedError,
)
from archivey.internal.streams.archive_stream import ArchiveStream  # noqa: E402
from archivey.internal.streams.streamtools.slice import SlicingStream  # noqa: E402
from archivey.internal.streams.verify import VerifyingStream  # noqa: E402

CHUNK = 64 * 1024


def med(fn, n: int = 15) -> float:
    xs: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs[1:])


def section_f1() -> None:
    print("=== F1: VerifyingStream.read(0) treated as EOF ===")
    payload = b"abc"
    crc = zlib.crc32(payload) & 0xFFFFFFFF

    try:
        VerifyingStream(io.BytesIO(payload), {"crc32": crc}).read(0)
        print("  hashed read(0): OK (unexpected — bug fixed?)")
    except CorruptionError as e:
        print(f"  hashed read(0): CorruptionError ({e})")

    s = VerifyingStream(io.BytesIO(payload), {}, expected_size=3)
    assert s.read(0) == b""
    try:
        s.close()
        print("  hashless read(0)+close: OK (unexpected — bug fixed?)")
    except TruncatedError as e:
        print(f"  hashless read(0)+close: TruncatedError ({e})")

    s = VerifyingStream(io.BytesIO(payload), {"crc32": crc})
    assert s.read(1) == b"a"
    try:
        s.read(0)
        print("  mid-stream read(0): OK (unexpected — bug fixed?)")
    except CorruptionError as e:
        print(f"  mid-stream read(0): CorruptionError ({e})")

    print(f"  BytesIO.read(0) reference: {io.BytesIO(payload).read(0)!r}")


def section_f2() -> None:
    print("=== F2: close leak on non-Corruption/Truncated ArchiveyError probe ===")

    class Boom(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"ab")
            self.close_called = False

        def read(self, n: int = -1) -> bytes:
            data = super().read(n)
            if not data:
                raise EncryptionError("boom")
            return data

        def close(self) -> None:
            self.close_called = True
            super().close()

    inner = Boom()
    s = VerifyingStream(inner, {}, expected_size=3)
    assert s.read(2) == b"ab"
    try:
        s.close()
        print("  close: OK (unexpected)")
    except EncryptionError as e:
        print(f"  close raised {type(e).__name__}: {e}")
    print(
        f"  wrapper.closed={s.closed} inner.closed={inner.closed} "
        f"close_called={inner.close_called}"
    )


def _dump(obj: object, depth: int = 0, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    name = type(obj).__name__
    extra = ""
    if isinstance(obj, VerifyingStream):
        extra = f" verify={obj._verify_enabled} pos={obj._pos}"
    if isinstance(obj, ArchiveStream):
        extra = f" size={obj._size} open_fn={'set' if obj._open_fn else None}"
    print("  " * depth + f"{name}{extra}")
    for attr in ("_inner", "_stream"):
        child = getattr(obj, attr, None)
        if child is not None and child is not obj:
            _dump(child, depth + 1, seen)


def section_stack() -> None:
    print("=== Live stack dump (after first read) ===")
    import archivey as _ay

    print(f"(archivey imported from {_ay.__file__})")
    with tempfile.TemporaryDirectory() as td:
        stored = Path(td) / "stored.zip"
        deflate = Path(td) / "deflate.zip"
        payload = b"x" * 65536
        with zipfile.ZipFile(stored, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("a.bin", payload)
        with zipfile.ZipFile(deflate, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.bin", payload)

        with open_archive(stored) as r:
            print("STORED open():")
            with r.open("a.bin") as f:
                f.read(1)
                _dump(f)
            print("STORED stream_members():")
            for _m, s in r.stream_members():
                if s is None:
                    continue
                s.read(1)
                _dump(s)
                break

        with open_archive(deflate) as r:
            print("DEFLATE open():")
            with r.open("a.bin") as f:
                f.read(1)
                _dump(f)


def _build_stored_zip(path: Path, members: int, size: int) -> list[int]:
    payloads = [os.urandom(size) for _ in range(members)]
    crcs = [zlib.crc32(p) & 0xFFFFFFFF for p in payloads]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i, p in enumerate(payloads):
            zf.writestr(f"m{i:03d}.bin", p)
    return crcs


def _regions(z: zipfile.ZipFile):
    fp = z.fp
    assert fp is not None
    for info in z.infolist():
        fp.seek(info.header_offset)
        hdr = fp.read(30)
        nlen, elen = struct.unpack_from("<HH", hdr, 26)
        yield info, info.header_offset + 30 + nlen + elen


class _Fused(ArchiveStream):
    """Minimal fused AS→Slice hasher for the synthetic comparison."""

    def __init__(self, inner: object, crc: int, size: int) -> None:
        self._exp_crc = crc
        self._exp = size
        self._pos = 0
        self._crc = 0
        self._done = False
        super().__init__(
            lambda: inner,  # type: ignore[arg-type,return-value]
            translate=lambda _e: None,
            lazy=False,
            size=size,
        )

    def read(self, n: int = -1) -> bytes:
        if self._done:
            return b""
        rem = self._exp - self._pos
        if rem <= 0:
            trailing = super().read(1)
            if trailing:
                raise ValueError("overlong")
            if (self._crc & 0xFFFFFFFF) != self._exp_crc:
                raise ValueError("crc")
            self._done = True
            return b""
        want = rem if n < 0 else min(n, rem)
        data = super().read(want)
        if data:
            self._crc = zlib.crc32(data, self._crc)
            self._pos += len(data)
            return data
        if (self._crc & 0xFFFFFFFF) != self._exp_crc:
            raise ValueError("crc")
        self._done = True
        return data


def section_stored() -> None:
    print("=== STORED ZIP isolation (64 × 256 KiB) ===")
    members, size = 64, 256 * 1024
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "stored.zip"
        crcs = _build_stored_zip(path, members, size)

        def ay() -> None:
            with open_archive(path) as r:
                for _m, s in r.stream_members():
                    if s is None:
                        continue
                    with s:
                        s.read()

        def zf() -> None:
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    with z.open(info) as f:
                        f.read()

        def current_like() -> None:
            with zipfile.ZipFile(path) as z:
                fp = z.fp
                assert fp is not None
                for i, (info, start) in enumerate(_regions(z)):
                    sl = SlicingStream(
                        fp, start=start, length=info.file_size, lock=z._lock
                    )
                    codec = ArchiveStream(
                        lambda sl=sl: sl, translate=lambda _e: None, lazy=False
                    )
                    vs = VerifyingStream(
                        codec, {"crc32": crcs[i]}, expected_size=info.file_size
                    )
                    outer = ArchiveStream(
                        lambda vs=vs: vs,
                        translate=lambda _e: None,
                        lazy=False,
                        size=info.file_size,
                    )
                    outer.read()
                    outer.close()

        def after_fuse() -> None:
            with zipfile.ZipFile(path) as z:
                fp = z.fp
                assert fp is not None
                for i, (info, start) in enumerate(_regions(z)):
                    sl = SlicingStream(
                        fp, start=start, length=info.file_size, lock=z._lock
                    )
                    vs = VerifyingStream(
                        sl, {"crc32": crcs[i]}, expected_size=info.file_size
                    )
                    outer = ArchiveStream(
                        lambda vs=vs: vs,
                        translate=lambda _e: None,
                        lazy=False,
                        size=info.file_size,
                    )
                    outer.read()
                    outer.close()

        def true_fused() -> None:
            with zipfile.ZipFile(path) as z:
                fp = z.fp
                assert fp is not None
                for i, (info, start) in enumerate(_regions(z)):
                    sl = SlicingStream(
                        fp, start=start, length=info.file_size, lock=z._lock
                    )
                    outer = _Fused(sl, crcs[i], info.file_size)
                    outer.read()
                    outer.close()

        rows = [
            ("archivey live", ay),
            ("zipfile", zf),
            ("current-like AS>VS>AS>Slice", current_like),
            ("after verify-fuse AS>VS>Slice", after_fuse),
            ("true fused AS>Slice", true_fused),
        ]
        results: dict[str, float] = {}
        for label, fn in rows:
            ms = med(fn)
            results[label] = ms
            print(f"  {label:<34} {ms:6.2f} ms")
        print(
            f"  STORED ratio archivey/zipfile: "
            f"{results['archivey live'] / results['zipfile']:.2f}x"
        )
        cur, fused = (
            results["current-like AS>VS>AS>Slice"],
            results["true fused AS>Slice"],
        )
        print(
            f"  synthetic fusion win vs current-like: "
            f"{(cur - fused) / cur * 100:.1f}% ({cur:.2f} → {fused:.2f} ms)"
        )
        print(
            f"  end-to-end win estimate if stack alone: "
            f"~{cur - fused:.2f} ms of {results['archivey live']:.2f} ms "
            f"({(cur - fused) / results['archivey live'] * 100:.1f}%)"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "sections",
        nargs="*",
        default=["all"],
        help="f1 f2 stack stored all",
    )
    args = p.parse_args(argv)
    wanted = set(args.sections)
    if "all" in wanted:
        wanted = {"f1", "f2", "stack", "stored"}
    if "f1" in wanted:
        section_f1()
        print()
    if "f2" in wanted:
        section_f2()
        print()
    if "stack" in wanted:
        section_stack()
        print()
    if "stored" in wanted:
        section_stored()
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
