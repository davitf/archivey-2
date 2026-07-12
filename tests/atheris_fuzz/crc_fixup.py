"""Mutate-then-fixup helpers for CRC-gated archive headers.

libFuzzer CMP feedback does not reliably synthesize CRC32 over a mutated header body
within short budgets. For targets whose interesting logic sits behind a header CRC we
recompute and patch the CRC fields after mutation so coverage reaches post-check paths.
A configurable minority of inputs keep deliberately broken CRCs so the reject path stays
exercised.
"""

from __future__ import annotations

import os
import struct
import zlib
from collections.abc import Callable

MAGIC_7Z = b"7z\xbc\xaf'\x1c"
_SIGNATURE_HEADER_SIZE = 32

# Fraction of inputs that keep a broken CRC (reject-path coverage). Override with
# ARCHIVEY_FUZZ_BROKEN_CRC_RATE (0.0–1.0).
_DEFAULT_BROKEN_CRC_RATE = 0.05


def broken_crc_rate() -> float:
    raw = os.environ.get("ARCHIVEY_FUZZ_BROKEN_CRC_RATE")
    if raw is None:
        return _DEFAULT_BROKEN_CRC_RATE
    try:
        rate = float(raw)
    except ValueError:
        return _DEFAULT_BROKEN_CRC_RATE
    return min(1.0, max(0.0, rate))


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def should_break_crc(data: bytes) -> bool:
    """Deterministic minority sampler from the input bytes (no extra RNG)."""
    rate = broken_crc_rate()
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    # Stable bucket in [0, 1) from a cheap mix of the blob.
    bucket = (_crc32(data) % 10_000) / 10_000.0
    return bucket < rate


def fixup_sevenzip_header_crcs(data: bytes, *, broken: bool | None = None) -> bytes:
    """Recompute 7z signature + next-header CRCs (or leave them broken).

    Layout (little-endian)::

        magic[6] | ver[2] | start_header_crc[4] | next_offset[8] | next_size[8] | next_crc[4]
        ... packed streams ...
        next_header[next_size]

    When ``broken`` is None, :func:`should_break_crc` decides. Broken mode flips the low
    bit of the patched next-header CRC after a correct recompute so the reject path sees
    a near-miss rather than an uninitialized field.
    """
    if len(data) < _SIGNATURE_HEADER_SIZE:
        return data
    if data[:6] != MAGIC_7Z:
        return data

    if broken is None:
        broken = should_break_crc(data)

    buf = bytearray(data)
    next_header_offset, next_header_size, _old_crc = struct.unpack(
        "<QQI", bytes(buf[12:32])
    )
    header_start = _SIGNATURE_HEADER_SIZE + next_header_offset
    header_end = header_start + next_header_size
    if (
        next_header_size == 0
        or header_end > len(buf)
        or header_start < _SIGNATURE_HEADER_SIZE
    ):
        # Cannot locate a next-header body; still refresh the start-header CRC over the
        # 20-byte start_header so signature-CRC reject stays distinct from next-header.
        start_header = bytes(buf[12:32])
        struct.pack_into("<I", buf, 8, _crc32(start_header))
        return bytes(buf)

    header_data = bytes(buf[header_start:header_end])
    next_crc = _crc32(header_data)
    if broken:
        next_crc ^= 1
    struct.pack_into("<I", buf, 28, next_crc)

    start_header = bytes(buf[12:32])
    start_crc = _crc32(start_header)
    if broken:
        # Keep start-header CRC valid so the failure is the next-header check (the gate
        # that hides post-parse bugs). Broken start-header CRC is a different, thinner path.
        pass
    struct.pack_into("<I", buf, 8, start_crc)
    return bytes(buf)


def fixup_zip_local_and_cd_crc(data: bytes, *, broken: bool | None = None) -> bytes:
    """Best-effort ZIP local-header + central-directory CRC32 patch for shallow open+list.

    Stdlib ``zipfile`` lists from the central directory; member CRC mismatches mainly
    surface on read. We still patch CD ``CRC-32`` (and matching local headers when the
    relative offset is sane) so wrapper paths behind those fields can be reached. This is
    intentionally conservative: malformed EOCD / spans return ``data`` unchanged.
    """
    if len(data) < 22:
        return data

    if broken is None:
        broken = should_break_crc(data)

    # Find the last EOCD signature (no zip64 locator walk for the shallow harness).
    eocd_sig = b"PK\x05\x06"
    idx = data.rfind(eocd_sig)
    if idx < 0 or idx + 22 > len(data):
        return data

    cd_size = int.from_bytes(data[idx + 12 : idx + 16], "little")
    cd_offset = int.from_bytes(data[idx + 16 : idx + 20], "little")
    if cd_offset + cd_size > len(data) or cd_size == 0:
        return data

    buf = bytearray(data)
    pos = cd_offset
    end = cd_offset + cd_size
    while pos + 46 <= end:
        if buf[pos : pos + 4] != b"PK\x01\x02":
            break
        crc_off = pos + 16
        comp_size = int.from_bytes(buf[pos + 20 : pos + 24], "little")
        uncomp_size = int.from_bytes(buf[pos + 24 : pos + 28], "little")
        name_len = int.from_bytes(buf[pos + 28 : pos + 30], "little")
        extra_len = int.from_bytes(buf[pos + 30 : pos + 32], "little")
        comment_len = int.from_bytes(buf[pos + 32 : pos + 34], "little")
        local_off = int.from_bytes(buf[pos + 42 : pos + 46], "little")

        # Prefer recomputing from the local compressed payload when the layout is sane;
        # otherwise leave the stored CRC and only optionally break it.
        new_crc: int | None = None
        if (
            local_off + 30 <= len(buf)
            and buf[local_off : local_off + 4] == b"PK\x03\x04"
        ):
            ln = int.from_bytes(buf[local_off + 26 : local_off + 28], "little")
            le = int.from_bytes(buf[local_off + 28 : local_off + 30], "little")
            payload_start = local_off + 30 + ln + le
            payload_end = payload_start + comp_size
            if payload_end <= len(buf) and uncomp_size == 0 and comp_size == 0:
                new_crc = 0
            elif payload_end <= len(buf):
                # Stored (method 0) payloads: CRC is over uncompressed == compressed bytes.
                method = int.from_bytes(buf[local_off + 8 : local_off + 10], "little")
                if method == 0:
                    new_crc = _crc32(bytes(buf[payload_start:payload_end]))

        if new_crc is not None:
            if broken:
                new_crc ^= 1
            struct.pack_into("<I", buf, crc_off, new_crc)
            # Mirror into the local header CRC field when present.
            struct.pack_into("<I", buf, local_off + 14, new_crc)

        pos += 46 + name_len + extra_len + comment_len

    return bytes(buf)


FixupFn = Callable[[bytes], bytes]


def apply_fixup(
    data: bytes,
    fixup: FixupFn | None,
    *,
    broken: bool | None = None,
) -> bytes:
    """Apply ``fixup`` when provided; pass ``broken`` through when the helper accepts it."""
    if fixup is None:
        return data
    # Prefer keyword form used by the helpers above.
    try:
        return fixup(data, broken=broken)  # type: ignore[call-arg]
    except TypeError:
        return fixup(data)
