"""Multi-volume path discovery and joining (7z concatenation; RAR join later)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeGuard

from archivey.internal.streams.streamtools import is_stream, source_name

SourceItem = str | Path | BinaryIO
SourceSequence = Sequence[SourceItem]

_7Z_VOLUME_RE = re.compile(r"^(?P<base>.+\.7z)\.(?P<part>\d+)$", re.IGNORECASE)
_RAR_PART_RE = re.compile(r"^(?P<base>.+)\.part(?P<part>\d+)\.rar$", re.IGNORECASE)
_RAR_RNN_RE = re.compile(r"^(?P<base>.+)\.r(?P<part>\d{2})$", re.IGNORECASE)


def _part_number_from_name(name: str, *, part_group: str = "part") -> int:
    match = re.search(rf"\.{part_group}(\d+)", name, re.IGNORECASE)
    if match is None:
        match = re.search(r"\.(\d+)$", name)
    return int(match.group(1)) if match is not None else 0


def _rnn_part_number(name: str) -> int:
    match = _RAR_RNN_RE.match(name)
    return int(match.group("part")) if match is not None else 0


def discover_volume_siblings(path: Path) -> list[Path] | None:
    """Return ordered sibling paths when ``path`` is part of a volume set, else ``None``."""
    if not path.is_file():
        return None
    parent = path.parent
    name = path.name

    match = _7Z_VOLUME_RE.match(name)
    if match is not None:
        base = match.group("base")
        siblings = sorted(
            (
                candidate
                for candidate in parent.iterdir()
                if candidate.is_file()
                and (vol_match := _7Z_VOLUME_RE.match(candidate.name)) is not None
                and vol_match.group("base").lower() == base.lower()
            ),
            key=lambda candidate: _part_number_from_name(candidate.name),
        )
        return siblings if len(siblings) > 1 else None

    match = _RAR_PART_RE.match(name)
    if match is not None:
        base = match.group("base")
        siblings = sorted(
            (
                candidate
                for candidate in parent.iterdir()
                if candidate.is_file()
                and (part_match := _RAR_PART_RE.match(candidate.name)) is not None
                and part_match.group("base").lower() == base.lower()
            ),
            key=lambda candidate: _part_number_from_name(
                candidate.name, part_group="part"
            ),
        )
        return siblings if len(siblings) > 1 else None

    if name.lower().endswith(".rar") and _RAR_PART_RE.match(name) is None:
        base = name[:-4]
        r00 = parent / f"{base}.r00"
        if r00.is_file():
            siblings = [path]
            siblings.extend(
                sorted(
                    (
                        candidate
                        for candidate in parent.iterdir()
                        if candidate.is_file()
                        and (rnn_match := _RAR_RNN_RE.match(candidate.name)) is not None
                        and rnn_match.group("base").lower() == base.lower()
                    ),
                    key=lambda candidate: _rnn_part_number(candidate.name),
                )
            )
            return siblings if len(siblings) > 1 else None

    match = _RAR_RNN_RE.match(name)
    if match is not None:
        base = match.group("base")
        first = parent / f"{base}.rar"
        # The first volume of an old-scheme set is always `<base>.rar`; the `.rNN`
        # files are its continuation volumes. Without the first volume present we can't
        # anchor the set at its head (siblings[0] must be volume 1), so a bare `.rNN`
        # with no `.rar` is treated as a lone file — mirrors the `.rar` branch above,
        # which requires `.r00` to exist.
        if not first.is_file():
            return None
        siblings: list[Path] = [first]
        siblings.extend(
            sorted(
                (
                    candidate
                    for candidate in parent.iterdir()
                    if candidate.is_file()
                    and (rnn_match := _RAR_RNN_RE.match(candidate.name)) is not None
                    and rnn_match.group("base").lower() == base.lower()
                ),
                key=lambda candidate: _rnn_part_number(candidate.name),
            )
        )
        return siblings if len(siblings) > 1 else None

    return None


OpenSourceInput = SourceItem | SourceSequence


@dataclass(frozen=True)
class ResolvedSource:
    """Single source to hand to detection/backends plus multi-volume metadata."""

    open_source: Path | BinaryIO
    archive_name: str | None
    volume_count: int


def _coerce_path_or_stream(item: SourceItem) -> Path | BinaryIO:
    if isinstance(item, (str, Path)):
        return Path(item)
    return item


def _is_source_sequence(source: OpenSourceInput) -> TypeGuard[SourceSequence]:
    if isinstance(source, (str, Path, bytes)):
        return False
    if is_stream(source):
        return False
    return isinstance(source, Sequence)


def resolve_source(source: OpenSourceInput) -> ResolvedSource:
    """Normalize ``source`` to one open target and record multi-volume detection."""
    if _is_source_sequence(source):
        items = [_coerce_path_or_stream(item) for item in source]
        if not items:
            raise ValueError("source sequence must not be empty")
        if len(items) == 1:
            return _resolve_single(items[0])
        first = items[0]
        return ResolvedSource(first, source_name(first), len(items))
    if isinstance(source, str):
        return _resolve_single(Path(source))
    if isinstance(source, Path):
        return _resolve_single(source)
    if not is_stream(source):
        raise TypeError(f"unsupported source type: {type(source)!r}")
    return _resolve_single(source)


def _resolve_single(source: Path | BinaryIO) -> ResolvedSource:
    if isinstance(source, Path):
        if source.is_dir():
            return ResolvedSource(source, str(source), 1)
        siblings = discover_volume_siblings(source)
        if siblings is not None:
            return ResolvedSource(siblings[0], source_name(siblings[0]), len(siblings))
        return ResolvedSource(source, source_name(source), 1)
    return ResolvedSource(source, source_name(source), 1)
