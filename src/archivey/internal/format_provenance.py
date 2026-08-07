"""How ``open_archive`` chose the format, kept for the empty-listing check.

An empty listing is only interesting when the format was **not confirmed by the
archive's bytes** — an explicit ``format=`` that skipped detection, or an extension
fallback that ran precisely because every content signal declined. Whether that was the
case is known at open; whether the listing is empty is not known until it completes. This
record carries the first fact forward to the place that learns the second
(``BaseArchiveReader._publish_materialized``).

It lives on the reader rather than in ``ReadBackend.open_read``'s signature because no
backend uses it: adding a parameter would touch every backend for a fact none of them
reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal


@dataclass(frozen=True)
class FormatProvenance:
    """Where the resolved ``ArchiveFormat`` came from."""

    chosen_by: Literal["argument", "extension", "content", "directory"]
    """``"content"`` covers magic, content probes and far magic — the bytes agreed.

    ``"extension"`` means detection fell through to the filename because magic, the
    content probes *and* far magic all declined, which is the same answer
    ``detect_format`` gives when it refuses a file outright.
    """

    source: Path | BinaryIO | None = None
    """The source as ``open_archive`` resolved it, for a re-detection pass.

    Only used for the ``"argument"`` case, which skipped detection and therefore has
    nothing recorded to compare against, and only when it is a :class:`Path`: reopening a
    file cannot disturb the reader, while seeking a live stream back to its origin can.
    """
