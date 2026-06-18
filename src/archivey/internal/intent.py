"""Intent enum and CostReceipt types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(Enum):
    # The two access *contracts* are DEFAULT/RANDOM (random access; fail fast on a
    # non-seekable source) vs SEQUENTIAL (forward-only, one pass). DEFAULT and RANDOM
    # share that contract and differ only in seek-point eagerness: DEFAULT builds them
    # lazily (on first seek), RANDOM proactively.
    DEFAULT = "default"
    SEQUENTIAL = "sequential"
    RANDOM = "random"


class ListingCost(Enum):
    INDEXED = "indexed"
    REQUIRES_SCANNING = "requires_scanning"
    REQUIRES_DECOMPRESSION = "requires_decompression"


class AccessCost(Enum):
    DIRECT = "direct"
    SOLID = "solid"


class StreamCapability(Enum):
    SEEKABLE = "seekable"
    FORWARD_ONLY = "forward_only"


@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    solid_block_count: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
