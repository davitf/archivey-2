"""Seed corpus for Atheris targets: declarative corpus + adversarial fixtures + tiny blobs."""

from __future__ import annotations

import gzip
import io
import lzma
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

from tests.sample_archives import CORPUS, corpus_archive_path

# Small corpus entry ids that build quickly and cover common layouts.
_SEED_ENTRY_IDS = frozenset(
    {"basic", "encoding", "large", "adversarial", "adversarial-tar"}
)
# Encrypted ZIP when the 7z CLI is available (AES / ZipCrypto fixtures).
_ENC_SEED_ENTRY_IDS = frozenset({"encrypted", "encrypted-mixed", "encrypted-multi"})

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADVERSARIAL_DIR = _REPO_ROOT / "tests" / "fixtures" / "adversarial"

_SAMPLE = b"atheris seed payload\n" * 8


def _corpus_seeds(
    format_key: str,
    tmp: Path,
    *,
    entry_ids: frozenset[str] = _SEED_ENTRY_IDS,
    allow_binaries: bool = False,
) -> list[bytes]:
    out: list[bytes] = []
    for entry in CORPUS:
        if entry.id not in entry_ids:
            continue
        if format_key not in entry.formats:
            continue
        if entry.requires_binaries and not allow_binaries:
            continue
        try:
            path = corpus_archive_path(entry, format_key, tmp)
        except (OSError, RuntimeError, ValueError, ImportError):
            continue
        if path.is_file():
            out.append(path.read_bytes())
    return out


def _adversarial_seeds(*suffixes: str) -> list[bytes]:
    if not _ADVERSARIAL_DIR.is_dir():
        return []
    out: list[bytes] = []
    for path in sorted(_ADVERSARIAL_DIR.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if suffixes and not any(name.endswith(s) for s in suffixes):
            continue
        try:
            out.append(path.read_bytes())
        except OSError:
            continue
    return out


def sevenzip_seeds() -> list[bytes]:
    from archivey.internal.backends.sevenzip_parser import MAGIC_7Z

    tiny = [
        b"",
        MAGIC_7Z,
        MAGIC_7Z + bytes(26),
        b"not a 7z archive",
        MAGIC_7Z + b"\x00\x04" + b"\xff" * 24,
    ]
    with tempfile.TemporaryDirectory(prefix="atheris-seed-7z-") as tmp:
        return tiny + _corpus_seeds("7z", Path(tmp)) + _adversarial_seeds(".7z")


def _synthetic_zip_seeds() -> list[bytes]:
    """Stored + deflate ZIPs so CRC fixup / native codec paths have known-good seeds."""
    out: list[bytes] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("stored.txt", _SAMPLE)
    out.append(buf.getvalue())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("deflate.txt", _SAMPLE * 4)
        zf.writestr("empty.txt", b"")
    out.append(buf.getvalue())
    return out


def zip_seeds() -> list[bytes]:
    tiny = [b"", b"PK\x03\x04", b"PK\x05\x06" + bytes(18), b"not a zip"]
    with tempfile.TemporaryDirectory(prefix="atheris-seed-zip-") as tmp:
        tmp_path = Path(tmp)
        corpus = _corpus_seeds("zip", tmp_path)
        # Encrypted / AES corpus when 7z can build them (optional).
        enc = _corpus_seeds(
            "zip",
            tmp_path,
            entry_ids=_ENC_SEED_ENTRY_IDS,
            allow_binaries=True,
        )
        return (
            tiny
            + _synthetic_zip_seeds()
            + corpus
            + enc
            + _adversarial_seeds(".zip")
        )


def tar_seeds() -> list[bytes]:
    tiny = [b"", b"ustar\x00", b"not a tar"]
    with tempfile.TemporaryDirectory(prefix="atheris-seed-tar-") as tmp:
        return (
            tiny
            + _corpus_seeds("tar", Path(tmp))
            + _corpus_seeds("tar.gz", Path(tmp))
            + _adversarial_seeds(".tar", ".tar.gz", ".tgz")
        )


def iso_seeds() -> list[bytes]:
    tiny = [b"", b"\x00" * 32768, b"not an iso"]
    with tempfile.TemporaryDirectory(prefix="atheris-seed-iso-") as tmp:
        return tiny + _corpus_seeds("iso", Path(tmp)) + _adversarial_seeds(".iso")


def rar_seeds() -> list[bytes]:
    """Tiny magics + on-disk RAR fixtures (RAR3/RAR5, solid, links, volumes, …)."""
    from archivey.internal.backends.rar_parser import RAR5_ID, RAR_ID

    tiny = [
        b"",
        RAR_ID,
        RAR5_ID,
        b"not rar",
        RAR_ID + b"\x00" * 32,
        RAR5_ID + b"\xff" * 64,
        # SFX-shaped: garbage prefix then magic (parser scans up to SFX_MAX).
        b"MZ" + b"\x00" * 64 + RAR5_ID + b"\x00" * 16,
    ]
    fixture_dir = _REPO_ROOT / "tests" / "fixtures" / "rar"
    fixtures: list[bytes] = []
    if fixture_dir.is_dir():
        for path in sorted(fixture_dir.glob("*.rar")):
            # Skip volume continuation alone — needs part1 as first volume.
            if path.name.endswith(".part2.rar") or ".part2." in path.name:
                continue
            # Header-encrypted archives need a password; still useful as reject-path seeds.
            try:
                fixtures.append(path.read_bytes())
            except OSError:
                continue
    return tiny + fixtures + _adversarial_seeds(".rar")


def detect_format_seeds() -> list[bytes]:
    """Mixed prefixes for ``detect_format`` — prefer short heads of real archives."""
    seeds: list[bytes] = [b"", b"\x00" * 16, b"PK", b"7z", b"Rar!", b"ustar"]
    for builder in (sevenzip_seeds, zip_seeds, tar_seeds, iso_seeds, rar_seeds):
        try:
            for blob in builder():
                seeds.append(blob[: min(len(blob), 512)])
                if len(blob) > 512:
                    seeds.append(blob[:4096])
        except (OSError, RuntimeError, ValueError, ImportError):
            continue
    # Dedup while preserving order.
    seen: set[bytes] = set()
    out: list[bytes] = []
    for s in seeds:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _safe_compress(fn: Callable[[], bytes]) -> list[bytes]:
    try:
        return [fn()]
    except (OSError, RuntimeError, ValueError, ImportError, lzma.LZMAError):
        return []


def unix_compress_seeds() -> list[bytes]:
    tiny = [b"", b"\x1f\x9d", b"\x1f\x9d\x90", b"not compress", b"\x1f\x9d" + b"\xff" * 64]
    good: list[bytes] = []
    try:
        from tests.streams_util import make_unix_compress

        good.append(make_unix_compress(_SAMPLE))
        good.append(make_unix_compress(b""))
    except (OSError, RuntimeError, ValueError, ImportError):
        pass
    return tiny + good + _adversarial_seeds(".Z", ".z")


def xz_seeds() -> list[bytes]:
    tiny = [b"", b"\xfd7zXZ\x00", b"not xz", b"\xfd7zXZ\x00" + b"\xff" * 32]
    good = _safe_compress(lambda: lzma.compress(_SAMPLE, format=lzma.FORMAT_XZ))
    return tiny + good + _adversarial_seeds(".xz")


def lzip_seeds() -> list[bytes]:
    tiny = [b"", b"LZIP", b"LZIP\x01", b"not lzip"]
    good: list[bytes] = []
    try:
        from tests.streams_util import make_lzip_member

        good.append(make_lzip_member(_SAMPLE))
    except (OSError, RuntimeError, ValueError, ImportError, lzma.LZMAError):
        pass
    return tiny + good + _adversarial_seeds(".lz")


def gzip_seeds() -> list[bytes]:
    tiny = [b"", b"\x1f\x8b", b"\x1f\x8b\x08", b"not gzip"]
    good = [gzip.compress(_SAMPLE), gzip.compress(b"")]
    return tiny + good + _adversarial_seeds(".gz")


def bzip2_seeds() -> list[bytes]:
    import bz2

    tiny = [b"", b"BZ", b"BZh", b"not bzip2"]
    good = [bz2.compress(_SAMPLE)]
    return tiny + good + _adversarial_seeds(".bz2", ".bz")


def lzma_alone_seeds() -> list[bytes]:
    tiny = [b"", b"]\x00\x00", b"not lzma"]
    good = _safe_compress(
        lambda: lzma.compress(_SAMPLE, format=lzma.FORMAT_ALONE)
    )
    return tiny + good + _adversarial_seeds(".lzma")


def zlib_seeds() -> list[bytes]:
    tiny = [b"", b"x\x9c", b"not zlib"]
    good = [zlib.compress(_SAMPLE)]
    return tiny + good


def zstd_seeds() -> list[bytes]:
    tiny = [b"", b"\x28\xb5\x2f\xfd", b"not zstd"]
    good: list[bytes] = []
    try:
        import zstandard

        good.append(zstandard.ZstdCompressor().compress(_SAMPLE))
    except (OSError, RuntimeError, ValueError, ImportError):
        pass
    return tiny + good + _adversarial_seeds(".zst", ".zstd")


def brotli_seeds() -> list[bytes]:
    tiny = [b"", b"not brotli", b"\x81"]
    good: list[bytes] = []
    try:
        import brotli

        good.append(brotli.compress(_SAMPLE))
    except (OSError, RuntimeError, ValueError, ImportError):
        pass
    return tiny + good


def lz4_seeds() -> list[bytes]:
    tiny = [b"", b"\x04\"M\x18", b"not lz4"]
    good: list[bytes] = []
    try:
        import lz4.frame

        good.append(lz4.frame.compress(_SAMPLE))
    except (OSError, RuntimeError, ValueError, ImportError):
        pass
    return tiny + good + _adversarial_seeds(".lz4")


def deflate64_seeds() -> list[bytes]:
    """Hostile / tiny blobs — real Deflate64 fixtures are rare; magics still useful."""
    tiny = [b"", b"not deflate64", b"\x00" * 16, b"\xff" * 64]
    # Prefer any on-disk fixtures that look like Deflate64 ZIP members if present.
    return tiny + _adversarial_seeds(".deflate64")


def write_seed_corpus(directory: Path, seeds: list[bytes]) -> int:
    """Write seed files for libFuzzer ``-seed_inputs`` / corpus dir. Returns count."""
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, seed in enumerate(seeds):
        path = directory / f"seed-{i:04d}"
        path.write_bytes(seed)
        written += 1
    return written
