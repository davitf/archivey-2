"""Mutation fuzzing over the declarative corpus (``testing-contract``: mutation robustness).

Every corpus archive is deterministically mutated — truncations, bit flips, zeroed
blocks, garbage prefixes/suffixes — and fed back through the full read path with the
**invariant**: archivey either succeeds or raises a typed ``ArchiveyError``; it never
raises a raw third-party/codec exception, never hangs (the suite-wide pytest-timeout
guards that), and never aborts the process. This is the pre-native-parser layer of the
fuzzing program (``docs/threat-model.md`` O5): the corpus doubles as the seed set, and
the same seeds feed the Phase-6 Atheris harnesses later.

Determinism: every mutation is derived from a seed of (entry id, format, harness
version), so a failure reproduces exactly; the failing mutation is described in the
assertion message (kind, offset) for standalone reproduction.

The format is passed explicitly to ``open_archive`` — a mutated magic must reach the
*parser*, not bounce off detection — and detection itself is exercised separately with
the mutated bytes.

**Scope: the deterministic parsing path.** The harness forces the ``[seekable]``
accelerators (``rapidgzip``/``indexed_bzip2``) **off**, so it exercises archivey's own
zero-dependency parsing and its exception-translation contract — the surface archivey
controls. Those C++ accelerators can *busy-loop* on crafted input (a hang no Python-level
translator can convert into an ``ArchiveyError``); fuzzing that native code needs a
resource-limited subprocess sandbox and belongs with the Phase-6 Atheris work, tracked as
a separate gap (``docs/threat-model.md`` O5 / C-accelerators).
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from random import Random

import pytest

from archivey import (
    AcceleratorMode,
    ArchiveyConfig,
    ArchiveyError,
    ExtractionLimits,
    OnError,
    OverwritePolicy,
    detect_format,
    open_archive,
)
from tests.sample_archives import (
    CORPUS,
    FORMAT_KEYS,
    CorpusEntry,
    corpus_archive_path,
)
from tests.test_corpus_sweep import _skip_unless_runnable

# Bump to change every derived mutation (a fresh sweep of the mutation space).
HARNESS_VERSION = 1

# Mutations per (archive × kind); the whole harness stays in the tens-of-milliseconds
# range per combination. Raise locally for a deeper run: ARCHIVEY_FUZZ_MUTATIONS=50.
_N = int(os.environ.get("ARCHIVEY_FUZZ_MUTATIONS", "6"))

# Cap what we pull from any (possibly lying) member stream, and keep extraction cheap.
_READ_CAP = 1 << 20
_FUZZ_LIMITS = ExtractionLimits(
    max_extracted_bytes=8 << 20, max_ratio=1000.0, max_entries=10_000
)

# Force the deterministic zero-dep codec path (see module docstring): the accelerators are
# third-party C++ and can hang on crafted input, which is out of scope for this layer.
_FUZZ_CONFIG = ArchiveyConfig(
    use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF
)

# Only file-backed formats are mutated ("dir" has no byte stream to corrupt).
_PARAMS = [
    pytest.param(entry, key, id=f"{entry.id}-{key}")
    for entry in CORPUS
    for key in entry.formats
    if key != "dir"
]


def _mutations(data: bytes, seed: str):
    """Yield (description, mutated_bytes): deterministic, format-agnostic corruptions."""
    rng = Random(f"{seed}|v{HARNESS_VERSION}")
    n = len(data)

    # Truncations: mid-header, mid-body, and just-shy-of-complete cuts.
    for frac in (0.02, 0.25, 0.5, 0.9, 0.99):
        cut = max(1, int(n * frac))
        yield f"truncate@{cut}", data[:cut]

    # Single bit flips at random offsets (headers and bodies alike).
    for _ in range(_N):
        pos = rng.randrange(n)
        bit = 1 << rng.randrange(8)
        yield f"bitflip@{pos}:{bit:#04x}", data[:pos] + bytes([data[pos] ^ bit]) + data[pos + 1 :]

    # Zeroed blocks (wipes length fields / magic / CRCs wholesale).
    for _ in range(_N // 2 or 1):
        pos = rng.randrange(n)
        size = min(rng.choice((4, 16, 64)), n - pos)
        yield f"zero@{pos}+{size}", data[:pos] + b"\x00" * size + data[pos + size :]

    # Garbage tail (trailing junk after a valid archive) and garbage head.
    junk = rng.randbytes(48)
    yield "append-junk", data + junk
    yield "prepend-junk", junk + data


def _exercise(mutated: bytes, entry: CorpusEntry, key: str, tmp_path: Path, desc: str) -> None:
    """The invariant: full read path over corrupted bytes -> success or ArchiveyError."""
    try:
        with open_archive(
            io.BytesIO(mutated),
            format=FORMAT_KEYS[key],
            password=list(entry.passwords) or None,
            config=_FUZZ_CONFIG,
        ) as ar:
            members = ar.members()
            for member in members:
                if not member.is_file:
                    continue
                try:
                    with ar.open(member) as f:
                        while f.read(1 << 16):
                            if f.tell() > _READ_CAP:
                                break
                except ArchiveyError:
                    pass  # per-member decode failure: exactly the contract
            ar.extract_all(
                tmp_path / "out",
                on_error=OnError.CONTINUE,
                overwrite=OverwritePolicy.REPLACE,
                limits=_FUZZ_LIMITS,
            )
    except ArchiveyError:
        return  # typed failure: the contract holds
    except Exception as exc:  # noqa: BLE001 - the harness exists to catch exactly this
        pytest.fail(
            f"invariant violated for {entry.id}-{key} mutation [{desc}]: "
            f"raw {type(exc).__name__}: {exc!r}"
        )


@pytest.mark.parametrize(("entry", "key"), _PARAMS)
def test_mutations_fail_typed_or_succeed(
    entry: CorpusEntry, key: str, tmp_path: Path
) -> None:
    _skip_unless_runnable(entry, key)
    source = corpus_archive_path(entry, key, tmp_path)
    data = source.read_bytes()

    for desc, mutated in _mutations(data, f"{entry.id}|{key}"):
        _exercise(mutated, entry, key, tmp_path, desc)

        # Detection must uphold the same invariant on arbitrary bytes (it may return
        # any format, or raise FormatDetectionError — never a raw codec exception).
        try:
            detect_format(io.BytesIO(mutated))
        except ArchiveyError:
            pass
        except Exception as exc:  # noqa: BLE001 - invariant check
            pytest.fail(
                f"detect_format raw exception for {entry.id}-{key} [{desc}]: "
                f"{type(exc).__name__}: {exc!r}"
            )
