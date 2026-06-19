"""Access-cost types: the `CostReceipt` and its enums.

Access *mode* is not modelled here — it is the plain ``streaming: bool`` parameter of
``open_archive`` (``False`` = random access, fail fast on a non-seekable source;
``True`` = forward-only, single pass). This module is only the cost half of the
``access-mode-and-cost`` capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ListingCost(Enum):
    """How expensive it is to *enumerate* all members (list names + metadata)."""

    INDEXED = "indexed"
    """An index / central directory is present; listing is O(1) regardless of archive size."""

    REQUIRES_SCANNING = "requires_scanning"
    """No index, but members can be enumerated by seeking/scanning header-to-header
    without decompressing payload (e.g. an uncompressed tar, or a RAR with no quick-open
    record)."""

    REQUIRES_DECOMPRESSION = "requires_decompression"
    """The stream must be decompressed to reach the member headers (e.g. a compressed tar)."""


class AccessCost(Enum):
    """How expensive it is to *read* one member's data, given the format layout."""

    DIRECT = "direct"
    """Any member can be read without touching other members."""

    SOLID = "solid"
    """Reading member N may require decompressing earlier members in its solid block."""


class StreamCapability(Enum):
    """A property of the underlying *source* bytes, independent of the format layout."""

    SEEKABLE = "seekable"
    """The source supports arbitrary ``seek()``; positions can be revisited."""

    FORWARD_ONLY = "forward_only"
    """Non-seekable source (pipe/socket): it cannot be rewound at all. Re-reading any
    earlier position requires a brand-new stream."""


@dataclass(frozen=True)
class CostReceipt:
    """Machine-readable description of an opened archive's access costs.

    The three axes are **orthogonal** and must not be conflated: ``listing_cost`` is about
    enumeration, ``access_cost`` about the format layout, and ``stream_capability`` about
    the source bytes. See the ``access-mode-and-cost`` capability spec for the full model.
    """

    listing_cost: ListingCost
    """Cost of enumerating all members."""

    access_cost: AccessCost
    """Cost of reading one member's data, given the format layout."""

    stream_capability: StreamCapability
    """Seekability of the underlying source bytes."""

    solid_block_count: int | None = None
    """Number of distinct solid blocks (each one decompress pass), or ``None`` when not
    applicable / unknown. ``is_solid`` lives on ``ArchiveInfo``, not here, to avoid
    duplicating the flag."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable caveats about the cost figures."""
