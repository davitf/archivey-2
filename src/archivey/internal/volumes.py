"""Multi-volume path discovery and joining (7z concatenation; RAR join later)."""

from __future__ import annotations

import bisect
import io
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeGuard

from archivey.exceptions import TruncatedError
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


class ConcatenatedFile(io.RawIOBase, BinaryIO):
    """Seekable read-only concatenation of volume streams."""

    def __init__(self, sources: Sequence[Path | BinaryIO]) -> None:
        super().__init__()
        if not sources:
            raise ValueError("at least one volume is required")
        self._streams: list[BinaryIO] = []
        self._owned: list[BinaryIO] = []
        offsets = [0]
        total = 0
        for source in sources:
            if isinstance(source, Path):
                stream = open(source, "rb")
                self._owned.append(stream)
            else:
                stream = source
            try:
                pos = stream.tell()
                size = stream.seek(0, os.SEEK_END)
                stream.seek(pos)
            except (OSError, AttributeError, io.UnsupportedOperation) as exc:
                raise ValueError("all volume streams must be seekable") from exc
            self._streams.append(stream)
            total += size
            offsets.append(total)
        self._offsets = offsets
        self._size = total
        self._pos = 0
        self.volume_count = len(sources)

    @property
    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == os.SEEK_END:
            new_pos = self._size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        if new_pos < 0:
            raise ValueError("Negative seek position")
        self._pos = new_pos
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if self._pos >= self._size:
            return b""
        if n is None or n < 0:
            n = self._size - self._pos
        else:
            n = min(n, self._size - self._pos)
        out = bytearray()
        while n > 0 and self._pos < self._size:
            index = bisect.bisect_right(self._offsets, self._pos) - 1
            stream = self._streams[index]
            volume_offset = self._pos - self._offsets[index]
            available = self._offsets[index + 1] - self._pos
            to_read = min(n, available)
            stream.seek(volume_offset)
            chunk = stream.read(to_read)
            if not chunk:
                break
            out.extend(chunk)
            self._pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def close(self) -> None:
        if self.closed:
            return
        try:
            for stream in self._owned:
                stream.close()
        finally:
            super().close()


def _validate_7z_volume_sequence(paths: Sequence[Path]) -> None:
    numbered: list[int] = []
    for path in paths:
        match = _7Z_VOLUME_RE.match(path.name)
        if match is None:
            return
        numbered.append(int(match.group("part")))
    expected = list(range(1, len(numbered) + 1))
    if numbered != expected:
        raise TruncatedError(
            f"Incomplete 7z multi-volume set: expected parts {expected}, got {numbered}"
        )


def join_volumes(paths: Sequence[Path]) -> BinaryIO:
    """Concatenate an ordered volume set into one seekable file-like object."""

    if not paths:
        raise ValueError("volume path sequence must not be empty")
    _validate_7z_volume_sequence(paths)
    return ConcatenatedFile(paths)


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
        if all(isinstance(item, Path) for item in items):
            paths = [item for item in items if isinstance(item, Path)]
            return ResolvedSource(join_volumes(paths), source_name(first), len(paths))
        return ResolvedSource(ConcatenatedFile(items), source_name(first), len(items))
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
            if _7Z_VOLUME_RE.match(siblings[0].name):
                return ResolvedSource(
                    join_volumes(siblings), source_name(siblings[0]), len(siblings)
                )
            return ResolvedSource(siblings[0], source_name(siblings[0]), len(siblings))
        return ResolvedSource(source, source_name(source), 1)
    return ResolvedSource(source, source_name(source), 1)
