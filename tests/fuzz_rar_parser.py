"""Env-gated mutation harness for the native RAR header parser.

Run locally with::

    ARCHIVEY_FUZZ=1 uv run --no-sync pytest tests/fuzz_rar_parser.py
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable
from pathlib import Path
from random import Random

import pytest

from archivey import ArchiveyError
from archivey.internal.backends.rar_parser import RAR5_ID, RAR_ID, parse_rar_archive

pytestmark = pytest.mark.skipif(
    os.environ.get("ARCHIVEY_FUZZ") != "1",
    reason="set ARCHIVEY_FUZZ=1 to run the RAR parser fuzz harness",
)

_HARNESS_VERSION = 1
_FIXTURES = Path(__file__).parent / "fixtures" / "rar"
_SEED_NAMES = (
    "basic_nonsolid__.rar",
    "basic_nonsolid__rar4.rar",
    "basic_solid__.rar",
    "comment__.rar",
    "stored_m0.rar",
)


def _seed_bytes() -> list[bytes]:
    seeds = [
        b"",
        RAR_ID,
        RAR5_ID,
        b"not a rar archive",
        RAR_ID + b"\x00" * 32,
        RAR5_ID + b"\xff" * 64,
    ]
    for name in _SEED_NAMES:
        path = _FIXTURES / name
        if path.is_file():
            seeds.append(path.read_bytes())
    return seeds


def _mutations(seed: bytes, label: str) -> Iterable[bytes]:
    yield seed
    if seed:
        yield seed[: max(1, len(seed) // 2)]
        yield seed + b"\x00" * 32
        rng = Random(f"rar-parser|{label}|v{_HARNESS_VERSION}")
        for _ in range(8):
            data = bytearray(seed)
            offset = rng.randrange(len(data))
            data[offset] ^= 1 << rng.randrange(8)
            yield bytes(data)


def test_parse_rar_archive_fuzz_harness() -> None:
    for index, seed in enumerate(_seed_bytes()):
        for mutated in _mutations(seed, str(index)):
            try:
                parse_rar_archive(io.BytesIO(mutated))
            except ArchiveyError:
                pass
