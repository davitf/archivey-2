"""Seed corpus for Atheris targets: declarative corpus + adversarial fixtures + tiny blobs."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.sample_archives import CORPUS, corpus_archive_path

# Small corpus entry ids that build quickly and cover common layouts.
_SEED_ENTRY_IDS = frozenset(
    {"basic", "encoding", "large", "adversarial", "adversarial-tar"}
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADVERSARIAL_DIR = _REPO_ROOT / "tests" / "fixtures" / "adversarial"


def _corpus_seeds(format_key: str, tmp: Path) -> list[bytes]:
    out: list[bytes] = []
    for entry in CORPUS:
        if entry.id not in _SEED_ENTRY_IDS:
            continue
        if format_key not in entry.formats:
            continue
        if entry.requires_binaries:
            # Encrypted / binary-gated fixtures are optional for the seed set.
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


def zip_seeds() -> list[bytes]:
    tiny = [b"", b"PK\x03\x04", b"PK\x05\x06" + bytes(18), b"not a zip"]
    with tempfile.TemporaryDirectory(prefix="atheris-seed-zip-") as tmp:
        return tiny + _corpus_seeds("zip", Path(tmp)) + _adversarial_seeds(".zip")


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


def detect_format_seeds() -> list[bytes]:
    """Mixed prefixes for ``detect_format`` — prefer short heads of real archives."""
    seeds: list[bytes] = [b"", b"\x00" * 16, b"PK", b"7z", b"Rar!", b"ustar"]
    for builder in (sevenzip_seeds, zip_seeds, tar_seeds, iso_seeds):
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


def rar_seeds() -> list[bytes]:
    # No native RAR backend yet; keep tiny magic-shaped seeds for when it registers.
    return [b"", b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00", b"not rar"]


def write_seed_corpus(directory: Path, seeds: list[bytes]) -> int:
    """Write seed files for libFuzzer ``-seed_inputs`` / corpus dir. Returns count."""
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, seed in enumerate(seeds):
        path = directory / f"seed-{i:04d}"
        path.write_bytes(seed)
        written += 1
    return written
