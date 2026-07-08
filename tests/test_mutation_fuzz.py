"""Mutation fuzzing over the declarative corpus (``testing-contract``: mutation robustness).

Every corpus archive is deterministically mutated — fixed-ratio truncations plus
header/trailer-biased random cuts, bit flips, zeroed blocks, garbage prefixes/suffixes
— and fed back through the full read path with the
**invariant**: archivey either succeeds or raises a typed ``ArchiveyError``; it never
raises a raw third-party/codec exception, never hangs (each parametrized case carries its
own ``pytest-timeout`` budget), never aborts the process, and a successful extraction must
leave the destination root a directory (replacing it with a file is a path-safety failure). This is the pre-native-parser
layer of the fuzzing program (``docs/threat-model.md`` O5): the corpus doubles as the seed
set, and the same seeds feed the Phase-6 Atheris harnesses later.

Tests are parametrized by ``(archive, format, mutation kind)`` so a hang or failure names a
single slice (e.g. ``adversarial-tar-tar.gz-bitflip``) and ``-k`` can target it without
running every other kind for that archive.

Determinism: every mutation is derived from a seed of ``(entry id, format, harness
version)``. Each mutation *kind* uses its own RNG sub-seed so raising
``ARCHIVEY_FUZZ_MUTATIONS`` only appends more cases of that kind and never perturbs
earlier ones. Bit flips, zero blocks, and junk are also reproducible from their
``[desc]`` alone (offset / mask / size / hex junk are embedded in the label).

Reproduce a single failure::

    ARCHIVEY_FUZZ_SELECT='bitflip@305:0x20' \\
    uv run pytest tests/test_mutation_fuzz.py -k adversarial-tar-tar.gz-bitflip

**Stateful-dest bugs:** the loop gives every mutation a fresh extraction directory and
checks that a successful extract leaves that root a directory (so a corrupt ``"."`` file
member is caught here). Re-extracting to an already-poisoned destination — raw
``FileExistsError`` from ``run()`` — still requires an explicit regression test; see
``test_mutated_archive_poisoned_dest_reextract``.

Run only one mutation kind across the corpus::

    uv run pytest tests/test_mutation_fuzz.py -k bitflip

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
import tempfile
from collections.abc import Iterator
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

# Per-(archive, format, mutation-kind) timeout (seconds). Deep sweeps may need more:
# ARCHIVEY_FUZZ_TIMEOUT=120 ARCHIVEY_FUZZ_MUTATIONS=2000 pytest ...
_FUZZ_TIMEOUT = float(os.environ.get("ARCHIVEY_FUZZ_TIMEOUT", "30"))

# Run only mutations whose description contains this substring (exact repro aid).
_FUZZ_SELECT = os.environ.get("ARCHIVEY_FUZZ_SELECT")

# Run only mutation kinds whose name contains this substring (e.g. ``bitflip``).
_FUZZ_KIND = os.environ.get("ARCHIVEY_FUZZ_KIND")

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

MUTATION_KINDS: tuple[str, ...] = ("truncate", "bitflip", "zero", "junk")

# Only file-backed formats are mutated ("dir" has no byte stream to corrupt).
_PARAMS = [
    pytest.param(entry, key, kind, id=f"{entry.id}-{key}-{kind}")
    for entry in CORPUS
    for key in entry.formats
    if key != "dir"
    for kind in MUTATION_KINDS
    if _FUZZ_KIND is None or _FUZZ_KIND in kind
]

def mutation_seed(entry: CorpusEntry, key: str) -> str:
    """Stable seed string for an (archive, format) pair."""
    return f"{entry.id}|{key}|v{HARNESS_VERSION}"


def mutation_kind(desc: str) -> str:
    """Map a mutation label to its parametrized kind."""
    if desc.startswith("truncate@"):
        return "truncate"
    if desc.startswith("bitflip@"):
        return "bitflip"
    if desc.startswith("zero@"):
        return "zero"
    if desc.startswith(("append-junk@", "prepend-junk@")):
        return "junk"
    raise ValueError(f"unknown mutation description: {desc!r}")


def _rng(seed: str, kind: str) -> Random:
    return Random(f"{seed}|{kind}")


def _biased_truncate_cut(rng: Random, n: int) -> int:
    """Return ``cut`` in ``[1, n - 1]`` biased toward header and trailer regions.

    ``data[:cut]`` drops the tail, so a low cut keeps only header bytes and a cut
    just shy of ``n`` chops the trailer/EOCD while leaving the header intact.
    """
    if n <= 1:
        return 1
    last = n - 1
    band = max(1, n // 8)
    roll = rng.random()
    if roll < 0.40:
        return rng.randrange(1, min(band, last) + 1)
    if roll < 0.80:
        low = max(1, last - band + 1)
        return rng.randrange(low, last + 1)
    mid_lo = min(band + 1, last)
    mid_hi = max(mid_lo, last - band)
    if mid_lo >= mid_hi:
        return rng.randrange(1, last + 1)
    return rng.randrange(mid_lo, mid_hi + 1)


def apply_mutation(data: bytes, desc: str) -> bytes:
    """Rebuild mutated bytes from a mutation label (no RNG needed)."""
    n = len(data)
    if desc.startswith("truncate@"):
        cut = int(desc.split("@", 1)[1])
        return data[:cut]
    if desc.startswith("bitflip@"):
        spec = desc.split("@", 1)[1]
        pos_s, bit_s = spec.split(":")
        pos = int(pos_s)
        bit = int(bit_s, 16)
        return data[:pos] + bytes([data[pos] ^ bit]) + data[pos + 1 :]
    if desc.startswith("zero@"):
        spec = desc.split("@", 1)[1]
        pos_s, size_s = spec.split("+")
        pos, size = int(pos_s), int(size_s)
        return data[:pos] + b"\x00" * size + data[pos + size :]
    if desc.startswith("append-junk@"):
        junk = bytes.fromhex(desc.split("@", 1)[1])
        return data + junk
    if desc.startswith("prepend-junk@"):
        junk = bytes.fromhex(desc.split("@", 1)[1])
        return junk + data
    raise ValueError(f"unknown mutation description: {desc!r}")


def _mutations_for_kind(data: bytes, seed: str, kind: str) -> Iterator[tuple[str, bytes]]:
    """Yield ``(description, mutated_bytes)`` for one mutation kind (deduplicated)."""
    n = len(data)

    if kind == "truncate":
        seen_cuts: set[int] = set()
        for frac in (0.02, 0.25, 0.5, 0.9, 0.99):
            cut = max(1, int(n * frac))
            if cut in seen_cuts:
                continue
            seen_cuts.add(cut)
            yield f"truncate@{cut}", data[:cut]
        if n <= 1:
            return
        target = min(len(seen_cuts) + _N, n - 1)
        if len(seen_cuts) >= target:
            return
        rng = _rng(seed, "truncate")
        remaining = target - len(seen_cuts)
        max_attempts = remaining + max(32, remaining * 4)
        attempts = 0
        while len(seen_cuts) < target and attempts < max_attempts:
            attempts += 1
            cut = _biased_truncate_cut(rng, n)
            if cut in seen_cuts:
                continue
            seen_cuts.add(cut)
            yield f"truncate@{cut}", data[:cut]
        return

    if kind == "bitflip":
        cap = n * 8  # one flip per (byte offset, bit mask)
        budget = min(_N, cap)
        if budget >= cap:
            for pos in range(n):
                for bit_i in range(8):
                    bit = 1 << bit_i
                    desc = f"bitflip@{pos}:{bit:#04x}"
                    yield (
                        desc,
                        data[:pos] + bytes([data[pos] ^ bit]) + data[pos + 1 :],
                    )
            return
        rng = _rng(seed, "bitflip")
        seen: set[str] = set()
        max_attempts = budget + max(64, budget * 4)
        attempts = 0
        while len(seen) < budget and attempts < max_attempts:
            attempts += 1
            pos = rng.randrange(n)
            bit = 1 << rng.randrange(8)
            desc = f"bitflip@{pos}:{bit:#04x}"
            if desc in seen:
                continue
            seen.add(desc)
            yield (
                desc,
                data[:pos] + bytes([data[pos] ^ bit]) + data[pos + 1 :],
            )
        return

    if kind == "zero":
        budget = min(_N // 2 or 1, n * 3)  # three block sizes: 4, 16, 64
        rng = _rng(seed, "zero")
        seen = set[str]()
        max_attempts = budget + max(32, budget * 4)
        attempts = 0
        while len(seen) < budget and attempts < max_attempts:
            attempts += 1
            pos = rng.randrange(n)
            size = min(rng.choice((4, 16, 64)), n - pos)
            desc = f"zero@{pos}+{size}"
            if desc in seen:
                continue
            seen.add(desc)
            yield desc, data[:pos] + b"\x00" * size + data[pos + size :]
        return

    if kind == "junk":
        rng = _rng(seed, "junk")
        junk = rng.randbytes(48)
        yield f"append-junk@{junk.hex()}", data + junk
        yield f"prepend-junk@{junk.hex()}", junk + data
        return

    raise ValueError(f"unknown mutation kind: {kind!r}")


def _mutations(data: bytes, seed: str) -> Iterator[tuple[str, bytes]]:
    """Yield every mutation across all kinds (harness helper / repro scripts)."""
    for kind in MUTATION_KINDS:
        yield from _mutations_for_kind(data, seed, kind)


def _failure_context(
    entry: CorpusEntry, key: str, kind: str, seed: str, desc: str
) -> str:
    return (
        f"invariant violated for {entry.id}-{key}-{kind} "
        f"seed={seed!r} mutation [{desc}]: "
        f"(repro: ARCHIVEY_FUZZ_SELECT={desc!r} "
        f"ARCHIVEY_FUZZ_MUTATIONS={_N} "
        f"uv run pytest tests/test_mutation_fuzz.py -k {entry.id}-{key}-{kind})"
    )


def _exercise(
    mutated: bytes,
    entry: CorpusEntry,
    key: str,
    out_dir: Path,
    kind: str,
    seed: str,
    desc: str,
) -> None:
    """The invariant: full read path over corrupted bytes -> success or ArchiveyError.

    On success the extraction root must remain a directory — replacing it with a file
    (e.g. a corrupt member named ``"."``) is treated as a path-safety failure.
    """
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
                out_dir,
                on_error=OnError.CONTINUE,
                overwrite=OverwritePolicy.REPLACE,
                limits=_FUZZ_LIMITS,
            )
    except ArchiveyError:
        return  # typed failure: the contract holds
    except Exception as exc:  # noqa: BLE001 - the harness exists to catch exactly this
        pytest.fail(
            f"{_failure_context(entry, key, kind, seed, desc)} "
            f"raw {type(exc).__name__}: {exc!r}"
        )

    if not out_dir.is_dir():
        pytest.fail(
            f"{_failure_context(entry, key, kind, seed, desc)} "
            f"extraction root is no longer a directory: {out_dir!r} "
            f"(is_file={out_dir.is_file()})"
        )


@pytest.mark.timeout(_FUZZ_TIMEOUT)
@pytest.mark.parametrize(("entry", "key", "kind"), _PARAMS)
def test_mutations_fail_typed_or_succeed(
    entry: CorpusEntry, key: str, kind: str, tmp_path: Path
) -> None:
    _skip_unless_runnable(entry, key)
    source = corpus_archive_path(entry, key, tmp_path)
    data = source.read_bytes()
    seed = mutation_seed(entry, key)

    for desc, mutated in _mutations_for_kind(data, seed, kind):
        if _FUZZ_SELECT is not None and _FUZZ_SELECT not in desc:
            continue
        # One extraction tree per mutation; deleted before the next iteration so deep
        # sweeps (ARCHIVEY_FUZZ_MUTATIONS=5000) do not accumulate under tmp_path.
        with tempfile.TemporaryDirectory(prefix="out-", dir=tmp_path) as out_raw:
            out_dir = Path(out_raw)
            _exercise(mutated, entry, key, out_dir, kind, seed, desc)

            # Detection must uphold the same invariant on arbitrary bytes (it may return
            # any format, or raise FormatDetectionError — never a raw codec exception).
            try:
                detect_format(io.BytesIO(mutated))
            except ArchiveyError:
                pass
            except Exception as exc:  # noqa: BLE001 - invariant check
                pytest.fail(
                    f"{_failure_context(entry, key, kind, seed, desc)} "
                    f"detect_format raw {type(exc).__name__}: {exc!r}"
                )


def test_mutated_archive_poisoned_dest_reextract(tmp_path: Path) -> None:
    """Regression: second ``extract_all`` to a dest already replaced by a file.

    The parametrized fuzz loop catches the poison itself (root no longer a directory);
    this test covers ``run()`` raising raw ``FileExistsError`` on re-extract.
    """
    entry = next(e for e in CORPUS if e.id == "adversarial-tar")
    key = "tar.gz"
    _skip_unless_runnable(entry, key)
    data = corpus_archive_path(entry, key, tmp_path).read_bytes()
    mutated = apply_mutation(data, "bitflip@107:0x10")
    dest = tmp_path / "out"
    dest.mkdir()
    config = ArchiveyConfig(
        use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF
    )
    limits = ExtractionLimits(
        max_extracted_bytes=8 << 20, max_ratio=1000.0, max_entries=10_000
    )

    with open_archive(
        io.BytesIO(mutated), format=FORMAT_KEYS[key], config=config
    ) as ar:
        ar.extract_all(
            dest,
            on_error=OnError.CONTINUE,
            overwrite=OverwritePolicy.REPLACE,
            limits=limits,
        )
    assert dest.is_file(), "fixture must poison the destination root"

    with open_archive(
        io.BytesIO(mutated), format=FORMAT_KEYS[key], config=config
    ) as ar:
        ar.extract_all(
            dest,
            on_error=OnError.CONTINUE,
            overwrite=OverwritePolicy.REPLACE,
            limits=limits,
        )
