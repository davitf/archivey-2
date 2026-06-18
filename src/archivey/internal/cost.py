"""Access-cost types: the `CostReceipt` and its enums.

Access *mode* is not modelled here — it is the plain ``streaming: bool`` parameter of
``open_archive`` (``False`` = random access, fail fast on a non-seekable source;
``True`` = forward-only, single pass). This module is only the cost half of the
``access-intent-and-cost`` capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
