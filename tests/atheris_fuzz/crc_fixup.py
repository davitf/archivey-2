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
    """Best-effort ZIP local-header + central-directory CRC32 patch for open+read.

    Stdlib ``zipfile`` lists from the central directory; member CRC mismatches mainly
    surface on read (archivey's native codec path verifies ``crc32``). We patch CD
    ``CRC-32`` (and matching local headers when the relative offset is sane) so
    post-CRC read paths can be reached.

    - Stored (method 0): CRC over the compressed (== uncompressed) payload.
    - Deflate (method 8): inflate the local payload with raw zlib; on success CRC over
      the decompressed bytes (covers header-only mutations that leave compressed data
      intact). Failed inflate leaves the stored CRC unchanged.
    - Other methods: leave CRC unchanged unless ``broken`` flips the existing field.

    Intentionally conservative: malformed EOCD / spans return ``data`` unchanged.
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
                method = int.from_bytes(buf[local_off + 8 : local_off + 10], "little")
                payload = bytes(buf[payload_start:payload_end])
                if method == 0:
                    new_crc = _crc32(payload)
                elif method == 8:
                    # Raw DEFLATE (ZIP); successful inflate → CRC of uncompressed bytes.
                    try:
                        new_crc = _crc32(zlib.decompress(payload, -zlib.MAX_WBITS))
                    except zlib.error:
                        new_crc = None

        if new_crc is not None:
            if broken:
                new_crc ^= 1
            struct.pack_into("<I", buf, crc_off, new_crc)
            struct.pack_into("<I", buf, local_off + 14, new_crc)
        elif broken:
            # No recompute path — still exercise reject by flipping the stored CRC.
            old = int.from_bytes(buf[crc_off : crc_off + 4], "little")
            struct.pack_into("<I", buf, crc_off, old ^ 1)
            if local_off + 18 <= len(buf) and buf[local_off : local_off + 4] == b"PK\x03\x04":
                struct.pack_into("<I", buf, local_off + 14, old ^ 1)

        pos += 46 + name_len + extra_len + comment_len

    return bytes(buf)


# ---------------------------------------------------------------------------
# RAR3 / RAR5 header CRC fixup
# ---------------------------------------------------------------------------

_RAR3_ID = b"Rar!\x1a\x07\x00"
_RAR5_ID = b"Rar!\x1a\x07\x01\x00"
_RAR_SFX_NEEDLE = b"Rar!\x1a\x07"
_RAR_SFX_MAX = 2 * 1024 * 1024

_RAR3_MARK = 0x72
_RAR3_MAIN = 0x73
_RAR3_FILE = 0x74
_RAR3_SUB = 0x7A
_RAR3_ENDARC = 0x7B
_RAR3_LONG_BLOCK = 0x8000
_RAR3_MAIN_ENCRYPTVER = 0x0200
_RAR3_MAIN_PASSWORD = 0x0080
_RAR3_FILE_LARGE = 0x0100
_RAR3_FILE_SALT = 0x0400
_RAR3_FILE_EXTTIME = 0x1000

_RAR5_ENDARC = 5
_RAR5_ENCRYPTION = 4
_RAR5_FLAG_EXTRA = 0x01
_RAR5_FLAG_DATA = 0x02
_RAR5_MAX_HEADER = 2 * 1024 * 1024

_S_RAR3_BLK = struct.Struct("<HBHH")  # crc16, type, flags, header_size
_S_RAR3_FILE = struct.Struct("<LLBLLBBHL")


def _find_rar_magic(data: bytes) -> tuple[int, bool] | None:
    """Return ``(offset, is_rar5)`` for the first RAR magic within the SFX scan limit."""
    limit = min(len(data), _RAR_SFX_MAX + len(_RAR5_ID))
    if data.startswith(_RAR5_ID):
        return 0, True
    if data.startswith(_RAR3_ID):
        return 0, False
    pos = 0
    while pos < limit:
        idx = data.find(_RAR_SFX_NEEDLE, pos, limit)
        if idx < 0:
            return None
        if data[idx : idx + len(_RAR5_ID)] == _RAR5_ID:
            return idx, True
        if data[idx : idx + len(_RAR3_ID)] == _RAR3_ID:
            return idx, False
        pos = idx + len(_RAR_SFX_NEEDLE)
    return None


def _load_vint_at(buf: bytes | bytearray, pos: int) -> tuple[int, int] | None:
    limit = min(pos + 11, len(buf))
    res = ofs = 0
    while pos < limit:
        b = buf[pos]
        res += (b & 0x7F) << ofs
        pos += 1
        ofs += 7
        if b < 0x80:
            return res, pos
    return None


def _rar3_ext_time_end(hdata: bytes, pos: int) -> int | None:
    """Advance past a RAR3 extended-time field; ``None`` if truncated."""
    if pos + 2 > len(hdata):
        return None
    flags = struct.unpack_from("<H", hdata, pos)[0]
    pos += 2
    for shift in (12, 8, 4, 0):
        flag = (flags >> shift) & 0xF
        if not (flag & 8):
            continue
        # First field may reuse DOS mtime (basetime present) and only store rem;
        # later fields include a DOS stamp when basetime is absent. Mirror parser:
        # mtime slot has basetime; others do not.
        has_basetime = shift == 12
        if not has_basetime:
            if pos + 4 > len(hdata):
                return None
            pos += 4
        cnt = flag & 3
        if pos + cnt > len(hdata):
            return None
        pos += cnt
    return pos


def _rar3_file_crc_pos(hdata: bytes, flags: int, *, is_service: bool) -> int | None:
    """Mirror ``rar_parser._parse_rar3_file_header`` CRC coverage end, or ``None``."""
    pos = _S_RAR3_BLK.size
    if flags & _RAR3_LONG_BLOCK:
        if pos + 4 > len(hdata):
            return None
        pos += 4
    # FILE re-reads pack_size as first field when LONG_BLOCK was set.
    file_pos = pos - 4 if (flags & _RAR3_LONG_BLOCK) else pos
    if file_pos + _S_RAR3_FILE.size > len(hdata):
        return None
    fld = _S_RAR3_FILE.unpack_from(hdata, file_pos)
    pos = file_pos + _S_RAR3_FILE.size
    name_size = fld[7]
    if flags & _RAR3_FILE_LARGE:
        if pos + 8 > len(hdata):
            return None
        pos += 8
    if pos + name_size > len(hdata):
        return None
    pos += name_size
    if flags & _RAR3_FILE_SALT:
        if pos + 8 > len(hdata):
            return None
        pos += 8
    if flags & _RAR3_FILE_EXTTIME:
        end = _rar3_ext_time_end(hdata, pos)
        if end is None:
            return None
        pos = end
    header_size = _S_RAR3_BLK.unpack_from(hdata, 0)[3]
    return header_size if is_service else pos


def _fixup_rar3_headers(buf: bytearray, start: int, *, broken: bool) -> None:
    pos = start + len(_RAR3_ID)
    # Cap walks so a hostile chain cannot hang the fixup itself.
    for _ in range(10_000):
        if pos + _S_RAR3_BLK.size > len(buf):
            return
        _header_crc, block_type, flags, header_size = _S_RAR3_BLK.unpack_from(buf, pos)
        if header_size < _S_RAR3_BLK.size or pos + header_size > len(buf):
            return
        hdata = memoryview(buf)[pos : pos + header_size]
        add_size = 0
        field_pos = _S_RAR3_BLK.size
        if flags & _RAR3_LONG_BLOCK:
            if field_pos + 4 > header_size:
                return
            add_size = struct.unpack_from("<I", hdata, field_pos)[0]
            field_pos += 4

        crc_pos: int | None
        if block_type == _RAR3_MAIN:
            field_pos += 6
            if flags & _RAR3_MAIN_ENCRYPTVER:
                field_pos += 1
            crc_pos = field_pos
        elif block_type == _RAR3_ENDARC:
            crc_pos = header_size
        elif block_type in (_RAR3_FILE, _RAR3_SUB):
            crc_pos = _rar3_file_crc_pos(
                bytes(hdata), flags, is_service=(block_type == _RAR3_SUB)
            )
        elif block_type == _RAR3_MARK:
            crc_pos = None  # leave MARK CRC alone
        else:
            # Unknown skippable: CRC usually covers [2:header_size].
            crc_pos = header_size

        if crc_pos is not None and 2 <= crc_pos <= header_size:
            new_crc = _crc32(bytes(hdata[2:crc_pos])) & 0xFFFF
            if broken:
                new_crc ^= 1
            struct.pack_into("<H", buf, pos, new_crc)

        next_pos = pos + header_size + add_size
        if next_pos <= pos:
            return
        pos = next_pos
        if block_type == _RAR3_ENDARC:
            return
        # Encrypted headers: ciphertext follows; stop rather than mis-patch.
        if block_type == _RAR3_MAIN and (flags & _RAR3_MAIN_PASSWORD):
            return


def _fixup_rar5_headers(buf: bytearray, start: int, *, broken: bool) -> None:
    pos = start + len(_RAR5_ID)
    for _ in range(10_000):
        if pos + 5 > len(buf):
            return
        vint = _load_vint_at(buf, pos + 4)
        if vint is None:
            return
        hdrlen, after_vint = vint
        if hdrlen > _RAR5_MAX_HEADER:
            return
        header_size = (after_vint - pos) + hdrlen
        if header_size < 5 or pos + header_size > len(buf):
            return

        new_crc = _crc32(bytes(buf[pos + 4 : pos + header_size]))
        if broken:
            new_crc ^= 1
        struct.pack_into("<I", buf, pos, new_crc)

        # Decode type/flags/add_size to skip packed data and stop at end/encryption.
        cursor = after_vint
        block_type_v = _load_vint_at(buf, cursor)
        if block_type_v is None:
            return
        block_type, cursor = block_type_v
        flags_v = _load_vint_at(buf, cursor)
        if flags_v is None:
            return
        block_flags, cursor = flags_v
        if block_flags & _RAR5_FLAG_EXTRA:
            extra_v = _load_vint_at(buf, cursor)
            if extra_v is None:
                return
            _extra, cursor = extra_v
        add_size = 0
        if block_flags & _RAR5_FLAG_DATA:
            add_v = _load_vint_at(buf, cursor)
            if add_v is None:
                return
            add_size, cursor = add_v

        next_pos = pos + header_size + add_size
        if next_pos <= pos:
            return
        pos = next_pos
        if block_type in (_RAR5_ENDARC, _RAR5_ENCRYPTION):
            return


def fixup_rar_header_crcs(data: bytes, *, broken: bool | None = None) -> bytes:
    """Recompute RAR3/RAR5 block header CRCs (or leave them broken).

    RAR5: CRC32 over the header bytes after the CRC field (exact).
    RAR3: CRC16 over ``hdata[2:crc_pos]`` using the same ``crc_pos`` rules as
    ``rar_parser`` (MAIN fields / FILE field walk / full ENDARC). Encrypted-header
    archives stop before the ciphertext so we do not invent plaintext CRCs.
    """
    located = _find_rar_magic(data)
    if located is None:
        return data

    if broken is None:
        broken = should_break_crc(data)

    start, is_rar5 = located
    buf = bytearray(data)
    if is_rar5:
        _fixup_rar5_headers(buf, start, broken=broken)
    else:
        _fixup_rar3_headers(buf, start, broken=broken)
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
