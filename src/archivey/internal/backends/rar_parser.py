"""Native RAR metadata parser (RAR 1.5 / 2.x through RAR5).

Parses archive headers into :class:`RarArchive` / :class:`RarMemberInfo` without
decompressing member data and without importing ``rarfile``. Header encryption uses
:mod:`archivey.internal.streams.crypto` (never ``cryptography`` directly).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from typing import BinaryIO, Protocol

from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.streams.crypto import AesParams, open_aes_decrypt_stage
from archivey.internal.streams.streamtools import read_exact


class _Readable(Protocol):
    def read(self, n: int = -1, /) -> bytes: ...
    def tell(self) -> int: ...
    def seek(self, offset: int, whence: int = 0, /) -> int: ...


# ---------------------------------------------------------------------------
# Magics / limits
# ---------------------------------------------------------------------------

RAR_ID = b"Rar!\x1a\x07\x00"
RAR5_ID = b"Rar!\x1a\x07\x01\x00"
_SFX_NEEDLE = b"Rar!\x1a\x07"
SFX_MAX = 2 * 1024 * 1024
_RAR_MAX_PASSWORD = 127
_RAR_MAX_KDF_SHIFT = 24
_RAR5_MAX_HEADER = 2 * 1024 * 1024

# RAR3 block types
_RAR3_MARK = 0x72
_RAR3_MAIN = 0x73
_RAR3_FILE = 0x74
_RAR3_OLD_COMMENT = 0x75
_RAR3_SUB = 0x7A
_RAR3_ENDARC = 0x7B

# RAR3 MAIN flags
_RAR3_MAIN_VOLUME = 0x0001
_RAR3_MAIN_COMMENT = 0x0002
_RAR3_MAIN_SOLID = 0x0008
_RAR3_MAIN_PASSWORD = 0x0080
_RAR3_MAIN_ENCRYPTVER = 0x0200

# RAR3 FILE flags
_RAR3_FILE_SPLIT_BEFORE = 0x0001
_RAR3_FILE_SPLIT_AFTER = 0x0002
_RAR3_FILE_PASSWORD = 0x0004
_RAR3_FILE_COMMENT = 0x0008
_RAR3_FILE_SOLID = 0x0010
_RAR3_FILE_DIRECTORY = 0x00E0
_RAR3_FILE_LARGE = 0x0100
_RAR3_FILE_UNICODE = 0x0200
_RAR3_FILE_SALT = 0x0400
_RAR3_FILE_VERSION = 0x0800
_RAR3_FILE_EXTTIME = 0x1000
_RAR3_LONG_BLOCK = 0x8000

_RAR3_OS_UNIX = 3
_RAR3_M0 = 0x30

# RAR5 block types / flags
_RAR5_MAIN = 1
_RAR5_FILE = 2
_RAR5_SERVICE = 3
_RAR5_ENCRYPTION = 4
_RAR5_ENDARC = 5

_RAR5_FLAG_EXTRA = 0x01
_RAR5_FLAG_DATA = 0x02
_RAR5_FLAG_SPLIT_BEFORE = 0x08
_RAR5_FLAG_SPLIT_AFTER = 0x10

_RAR5_MAIN_ISVOL = 0x01
_RAR5_MAIN_HAS_VOLNR = 0x02
_RAR5_MAIN_SOLID = 0x04

_RAR5_FILE_ISDIR = 0x01
_RAR5_FILE_HAS_MTIME = 0x02
_RAR5_FILE_HAS_CRC32 = 0x04

_RAR5_COMPR_SOLID = 0x40

_RAR5_ENC_HAS_CHECKVAL = 0x01
_RAR5_XENC_CHECKVAL = 0x01
_RAR5_XENC_TWEAKED = 0x02
_RAR5_XENC_AES256 = 0

_RAR5_XFILE_ENCRYPTION = 1
_RAR5_XFILE_HASH = 2
_RAR5_XFILE_TIME = 3
_RAR5_XFILE_VERSION = 4
_RAR5_XFILE_REDIR = 5
_RAR5_XFILE_OWNER = 6

_RAR5_XTIME_UNIXTIME = 0x01
_RAR5_XTIME_HAS_MTIME = 0x02
_RAR5_XTIME_HAS_CTIME = 0x04
_RAR5_XTIME_HAS_ATIME = 0x08
_RAR5_XTIME_UNIXTIME_NS = 0x10

_RAR5_XHASH_BLAKE2SP = 0

_RAR5_XREDIR_UNIX_SYMLINK = 1
_RAR5_XREDIR_WINDOWS_SYMLINK = 2
_RAR5_XREDIR_WINDOWS_JUNCTION = 3
_RAR5_XREDIR_HARD_LINK = 4
_RAR5_XREDIR_FILE_COPY = 5

_RAR5_ENDARC_NEXT_VOLUME = 0x01

_RAR3_ENDARC_NEXT_VOLUME = 0x0001

_RAR5_OS_WINDOWS = 0
_RAR5_OS_UNIX = 1

_S_BLK_HDR = struct.Struct("<HBHH")
_S_FILE_HDR = struct.Struct("<LLBLLBBHL")
_S_LONG = struct.Struct("<L")
_S_SHORT = struct.Struct("<H")

_TRY_ENCODINGS = ("utf8", "utf-16le", "windows-1252")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RarEncryptionInfo:
    algo: int
    flags: int
    kdf_count: int
    salt: bytes
    iv: bytes | None  # file-level has IV; header enc block may not
    check_value: bytes | None


@dataclass
class RarMemberInfo:
    filename: str
    orig_filename: bytes | None
    file_size: int
    compress_size: int
    compress_type: int | None  # 0x30..0x35
    crc32: int | None
    blake2sp_hash: bytes | None
    mtime: datetime | None  # RAR4 naive; RAR5 aware UTC
    mode: int | None
    host_os: int | None
    flags: int
    file_redir: tuple[int, int, str] | None  # type, flags, target
    file_encryption: RarEncryptionInfo | None
    header_offset: int
    header_size: int
    data_offset: int
    extract_version: int | None
    file_solid: bool
    is_directory: bool
    is_symlink: bool  # RAR4 unix mode or RAR5 redir symlink types
    is_hardlink_or_copy: bool  # RAR5 HARD_LINK or FILE_COPY
    is_encrypted: bool
    volume_index: int
    split_before: bool
    split_after: bool
    comment: str | None = None
    spanned_volumes: bool = False

    def needs_password(self) -> bool:
        return self.is_encrypted

    def is_payload_file(self) -> bool:
        """True if ``unrar p`` emits this member's bytes (regular file, not dir/link/redir)."""
        return not (self.is_directory or self.is_symlink or self.is_hardlink_or_copy)


@dataclass
class RarArchive:
    version: int  # 4 or 5 (use 4 for RAR3 on-disk format)
    is_solid: bool
    has_header_encryption: bool
    comment: str | None
    members: list[RarMemberInfo]
    sfx_offset: int
    is_volume: bool
    needs_next_volume: bool = False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_rar_archive(
    source: BinaryIO,
    *,
    password: str | bytes | None = None,
) -> RarArchive:
    """Parse from current position (archive start). Source must be seekable."""
    return _parse_rar_one(
        source, password=password, volume_index=0, allow_continuation=False
    )


def parse_rar_volumes(
    volumes: Sequence[BinaryIO],
    *,
    password: str | bytes | None = None,
) -> RarArchive:
    """Parse an ordered multi-volume RAR set, merging split members across volumes.

    Each volume is an independent seekable stream positioned at its start. Member
    ``header_offset`` / ``data_offset`` values are adjusted to a concatenated byte
    space (volume 0 at 0, volume 1 after volume 0's size, …) so a
    :class:`~archivey.internal.volumes.ConcatenatedFile` can serve stored reads.
    """
    if not volumes:
        raise ValueError("at least one RAR volume is required")

    merged: RarArchive | None = None
    base_offset = 0
    for index, volume in enumerate(volumes):
        part = _parse_rar_one(
            volume,
            password=password,
            volume_index=index,
            allow_continuation=index > 0,
        )
        # Reject sets that do not start at volume 1.
        if index == 0 and (
            (part.members and part.members[0].split_before)
            or any(m.split_before and m.volume_index == 0 for m in part.members)
        ):
            raise UnsupportedFeatureError(
                "Need first volume of multi-volume RAR archive"
            )

        for member in part.members:
            member.header_offset += base_offset
            member.data_offset += base_offset

        if merged is None:
            merged = part
        else:
            if part.version != merged.version:
                raise CorruptionError(
                    f"RAR volume version mismatch: {merged.version} vs {part.version}"
                )
            merged.is_solid = merged.is_solid or part.is_solid
            merged.has_header_encryption = (
                merged.has_header_encryption or part.has_header_encryption
            )
            if part.comment and not merged.comment:
                merged.comment = part.comment
            merged.is_volume = True
            for member in part.members:
                if member.split_before and merged.members:
                    _merge_split_member(merged.members[-1], member)
                else:
                    merged.members.append(member)

        # Size of this volume for absolute offset adjustment.
        pos = volume.tell()
        end = volume.seek(0, 2)
        volume.seek(pos)
        base_offset += end

        if part.needs_next_volume:
            if index + 1 >= len(volumes):
                raise TruncatedError(
                    "Incomplete RAR multi-volume set: end of archive expects another volume"
                )
            continue

        # Archive is complete; ignore trailing unused volume paths if any were listed.
        merged.needs_next_volume = False
        return merged

    assert merged is not None
    if merged.needs_next_volume:
        raise TruncatedError(
            "Incomplete RAR multi-volume set: end of archive expects another volume"
        )
    return merged


def _parse_rar_one(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    volume_index: int,
    allow_continuation: bool,
) -> RarArchive:
    start = source.tell()
    version, sfx_offset = _find_sfx_header(source, start)
    source.seek(start + sfx_offset)
    if version == 5:
        archive = _parse_rar5(
            source,
            password=password,
            sfx_offset=sfx_offset,
            volume_index=volume_index,
        )
    else:
        archive = _parse_rar3(
            source,
            password=password,
            sfx_offset=sfx_offset,
            volume_index=volume_index,
        )
    if (
        not allow_continuation
        and archive.members
        and archive.members[0].split_before
        and volume_index == 0
    ):
        raise UnsupportedFeatureError("Need first volume of multi-volume RAR archive")
    return archive


# ---------------------------------------------------------------------------
# SFX detection
# ---------------------------------------------------------------------------


def _find_sfx_header(source: BinaryIO, start: int) -> tuple[int, int]:
    """Return ``(version, offset_from_start)`` for RAR4 (version 4) or RAR5."""
    source.seek(start)
    # Fast path: magic at current position.
    head = read_exact(source, len(RAR5_ID))
    if head.startswith(RAR5_ID):
        return 5, 0
    if head.startswith(RAR_ID):
        return 4, 0

    source.seek(start)
    buf = bytearray()
    remaining = SFX_MAX
    while remaining > 0:
        chunk = source.read(min(65536, remaining))
        if not chunk:
            break
        buf.extend(chunk)
        remaining -= len(chunk)
        # Search within newly complete window.
        find_from = max(0, len(buf) - len(chunk) - len(_SFX_NEEDLE))
        pos = find_from
        while True:
            idx = bytes(buf).find(_SFX_NEEDLE, pos)
            if idx < 0:
                break
            if buf[idx : idx + len(RAR5_ID)] == RAR5_ID:
                return 5, idx
            if buf[idx : idx + len(RAR_ID)] == RAR_ID:
                return 4, idx
            pos = idx + len(_SFX_NEEDLE)

    raise CorruptionError("Not a RAR archive: magic not found within SFX scan limit")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _crc32(data: bytes | memoryview) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _require_exact(stream: BinaryIO, n: int, what: str) -> bytes:
    data = read_exact(stream, n)
    if len(data) != n:
        raise CorruptionError(f"Unexpected EOF while reading {what}")
    return data


def _load_vint(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    limit = min(pos + 11, len(buf))
    res = ofs = 0
    while pos < limit:
        b = buf[pos]
        res += (b & 0x7F) << ofs
        pos += 1
        ofs += 7
        if b < 0x80:
            return res, pos
    raise CorruptionError("Invalid RAR5 variable-length integer")


def _load_byte(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    if pos >= len(buf):
        raise CorruptionError("Unexpected EOF while reading byte")
    return buf[pos], pos + 1


def _load_le32(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    end = pos + 4
    if end > len(buf):
        raise CorruptionError("Unexpected EOF while reading le32")
    return _S_LONG.unpack_from(buf, pos)[0], end


def _load_bytes(
    buf: bytes | bytearray | memoryview, num: int, pos: int
) -> tuple[bytes, int]:
    end = pos + num
    if end > len(buf):
        raise CorruptionError("Unexpected EOF while reading bytes")
    return bytes(buf[pos:end]), end


def _load_vstr(buf: bytes | bytearray | memoryview, pos: int) -> tuple[bytes, int]:
    slen, pos = _load_vint(buf, pos)
    return _load_bytes(buf, slen, pos)


def _parse_dos_time(stamp: int) -> datetime:
    sec = (stamp & 0x1F) * 2
    stamp >>= 5
    minute = stamp & 0x3F
    stamp >>= 6
    hour = stamp & 0x1F
    stamp >>= 5
    day = stamp & 0x1F
    stamp >>= 5
    month = stamp & 0x0F
    stamp >>= 4
    year = (stamp & 0x7F) + 1980
    try:
        return datetime(year, month, day, hour, minute, sec)
    except ValueError:
        month = max(1, min(month, 12))
        mday = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        day = max(1, min(day, mday[month]))
        hour = min(hour, 23)
        minute = min(minute, 59)
        sec = min(sec, 59)
        return datetime(year, month, day, hour, minute, sec)


def _load_unixtime(
    buf: bytes | bytearray | memoryview, pos: int
) -> tuple[datetime, int]:
    secs, pos = _load_le32(buf, pos)
    return datetime.fromtimestamp(secs, timezone.utc), pos


def _load_windowstime(
    buf: bytes | bytearray | memoryview, pos: int
) -> tuple[datetime, int]:
    # Windows FILETIME: 100ns since 1601-01-01.
    lo, pos = _load_le32(buf, pos)
    hi, pos = _load_le32(buf, pos)
    ticks = (hi << 32) | lo
    # unix epoch (1970) in 100ns units from windows epoch (1601)
    unix_ticks = ticks - 116444736000000000
    secs, rem = divmod(unix_ticks, 10_000_000)
    dt = datetime.fromtimestamp(secs, timezone.utc)
    if rem:
        dt = dt.replace(microsecond=rem // 10)
    return dt, pos


def _normalize_password_utf8(password: str | bytes) -> bytes:
    """RAR password normalization: UTF-16LE truncate → UTF-8."""
    if isinstance(password, bytes):
        pwd = password.decode("utf8")
    else:
        pwd = password
    wstr = pwd.encode("utf-16le")[: _RAR_MAX_PASSWORD * 2]
    return wstr.decode("utf-16le").encode("utf8")


def _normalize_password_utf16le(password: str | bytes) -> bytes:
    if isinstance(password, bytes):
        pwd = password.decode("utf8")
    else:
        pwd = password
    return pwd.encode("utf-16le")[: _RAR_MAX_PASSWORD * 2]


def _decode_name(raw: bytes) -> str:
    for enc in _TRY_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("windows-1252", "replace")


def _merge_split_member(old: RarMemberInfo, new: RarMemberInfo) -> None:
    """Collapse a SPLIT_AFTER continuation into the first part (rarfile-style)."""
    old.compress_size += new.compress_size
    if new.crc32 is not None:
        old.crc32 = new.crc32
    if new.blake2sp_hash is not None:
        old.blake2sp_hash = new.blake2sp_hash
    old.split_after = new.split_after
    old.spanned_volumes = True


# ---------------------------------------------------------------------------
# Header decrypt stream (AES-CBC via crypto module)
# ---------------------------------------------------------------------------


class _HeaderDecryptStream:
    """Decrypt subsequent header bytes with AES-CBC; seek/tell pass through."""

    def __init__(self, source: BinaryIO, key: bytes, iv: bytes) -> None:
        self._source = source
        self._stage = open_aes_decrypt_stage(AesParams(key=key, iv=iv))
        self._buf = bytearray()

    def tell(self) -> int:
        return self._source.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        self._buf.clear()
        return self._source.seek(offset, whence)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            raise CorruptionError("Unbounded read on encrypted RAR header stream")
        if n > 8 * 1024:
            raise CorruptionError(
                "Encrypted RAR header read too large — wrong password?"
            )
        if n <= len(self._buf):
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out
        out = bytearray(self._buf)
        self._buf.clear()
        need = n - len(out)
        while need > 0:
            enc = self._source.read(16)
            if len(enc) < 16:
                break
            dec = self._stage.update(enc)
            if need >= len(dec):
                out.extend(dec)
                need -= len(dec)
            else:
                out.extend(dec[:need])
                self._buf.extend(dec[need:])
                need = 0
        return bytes(out)


# ---------------------------------------------------------------------------
# RAR3 SHA-1 / string-to-key (ported from rarfile 4.3)
# ---------------------------------------------------------------------------


class _Rar3Sha1:
    """Emulate the buggy SHA-1 used by RAR3 key derivation."""

    _BLK_BE = struct.Struct(b">16L")
    _BLK_LE = struct.Struct(b"<16L")
    block_size = 64

    def __init__(self, *, rarbug: bool = False) -> None:
        self._md = hashlib.sha1()
        self._nbytes = 0
        self._rarbug = rarbug

    def update(self, data: bytes | bytearray) -> None:
        self._md.update(data)
        bufpos = self._nbytes & 63
        self._nbytes += len(data)
        if self._rarbug and len(data) > 64:
            dpos = self.block_size - bufpos
            while dpos + self.block_size <= len(data):
                self._corrupt(data, dpos)
                dpos += self.block_size

    def digest(self) -> bytes:
        return self._md.digest()

    def _corrupt(self, data: bytes | bytearray, dpos: int) -> None:
        if not isinstance(data, bytearray):
            return
        ws = list(self._BLK_BE.unpack_from(data, dpos))
        for t in range(16, 80):
            tmp = (
                ws[(t - 3) & 15]
                ^ ws[(t - 8) & 15]
                ^ ws[(t - 14) & 15]
                ^ ws[(t - 16) & 15]
            )
            ws[t & 15] = ((tmp << 1) | (tmp >> 31)) & 0xFFFFFFFF
        self._BLK_LE.pack_into(data, dpos, *ws)


def _rar3_s2k(password: str | bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive AES-128 key + IV for RAR3 header/file encryption."""
    wstr = _normalize_password_utf16le(password)
    seed = bytearray(wstr + salt)
    h = _Rar3Sha1(rarbug=True)
    iv = bytearray()
    for i in range(16):
        for j in range(0x4000):
            cnt = struct.pack("<L", i * 0x4000 + j)
            h.update(seed)
            h.update(cnt[:3])
            if j == 0:
                iv.append(h.digest()[19])
    key_be = h.digest()[:16]
    key_le = struct.pack("<LLLL", *struct.unpack(">LLLL", key_be))
    return key_le, bytes(iv)


def _rar5_s2k(password: str | bytes, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256 for RAR5 (returns 32-byte AES-256 key material)."""
    ustr = _normalize_password_utf8(password)
    return pbkdf2_hmac("sha256", ustr, salt, iterations, dklen=32)


# ---------------------------------------------------------------------------
# RAR3 Unicode filename decompressor (ported from rarfile)
# ---------------------------------------------------------------------------


class _UnicodeFilename:
    def __init__(self, name: bytes, encdata: bytes) -> None:
        self.std_name = bytearray(name)
        self.encdata = bytearray(encdata)
        self.pos = 0
        self.encpos = 0
        self.buf = bytearray()
        self.failed = False

    def _enc_byte(self) -> int:
        try:
            c = self.encdata[self.encpos]
            self.encpos += 1
            return c
        except IndexError:
            self.failed = True
            return 0

    def _std_byte(self) -> int:
        try:
            return self.std_name[self.pos]
        except IndexError:
            self.failed = True
            return ord("?")

    def _put(self, lo: int, hi: int) -> None:
        self.buf.append(lo)
        self.buf.append(hi)
        self.pos += 1

    def decode(self) -> str:
        hi = self._enc_byte()
        flagbits = 0
        flags = 0
        while self.encpos < len(self.encdata):
            if flagbits == 0:
                flags = self._enc_byte()
                flagbits = 8
            flagbits -= 2
            t = (flags >> flagbits) & 3
            if t == 0:
                self._put(self._enc_byte(), 0)
            elif t == 1:
                self._put(self._enc_byte(), hi)
            elif t == 2:
                self._put(self._enc_byte(), self._enc_byte())
            else:
                n = self._enc_byte()
                if n & 0x80:
                    c = self._enc_byte()
                    for _ in range((n & 0x7F) + 2):
                        lo = (self._std_byte() + c) & 0xFF
                        self._put(lo, hi)
                else:
                    for _ in range(n + 2):
                        self._put(self._std_byte(), 0)
        return self.buf.decode("utf-16le", "replace")


# ---------------------------------------------------------------------------
# RAR3 / RAR4 parser
# ---------------------------------------------------------------------------


@dataclass
class _Rar3EncState:
    password: str | bytes
    last_salt: bytes | None = None
    last_key: bytes | None = None
    last_iv: bytes | None = None


def _parse_rar3(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    sfx_offset: int,
    volume_index: int = 0,
) -> RarArchive:
    _require_exact(source, len(RAR_ID), "RAR3 signature")

    is_solid = False
    is_volume = False
    has_header_encryption = False
    comment: str | None = None
    members: list[RarMemberInfo] = []
    enc_state: _Rar3EncState | None = None
    needs_next_volume = False

    while True:
        header_fd: _Readable = source
        if has_header_encryption:
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            if enc_state is None:
                enc_state = _Rar3EncState(password=password)
            try:
                header_fd = _rar3_decrypt_header(source, enc_state)
            except PackageNotInstalledError:
                raise
            except Exception as exc:
                raise EncryptionError(f"Failed to decrypt RAR3 headers: {exc}") from exc

        header_offset = header_fd.tell()
        buf = header_fd.read(_S_BLK_HDR.size)
        if not buf:
            break
        if len(buf) < _S_BLK_HDR.size:
            raise CorruptionError("Unexpected EOF while reading RAR3 block header")

        header_crc, block_type, flags, header_size = _S_BLK_HDR.unpack_from(buf)
        if header_size < _S_BLK_HDR.size:
            raise CorruptionError(f"Invalid RAR3 header size: {header_size}")
        if header_size > _S_BLK_HDR.size:
            rest = header_fd.read(header_size - _S_BLK_HDR.size)
            if len(rest) != header_size - _S_BLK_HDR.size:
                raise CorruptionError("Unexpected EOF while reading RAR3 header body")
            hdata = buf + rest
        else:
            hdata = buf
        # HeaderDecryptStream.tell() reports the underlying ciphertext position
        # (including AES block padding), which is the correct data_offset.
        data_offset = header_fd.tell()

        pos = _S_BLK_HDR.size
        if flags & _RAR3_LONG_BLOCK:
            add_size, pos = _load_le32(hdata, pos)
        else:
            add_size = 0

        if block_type == _RAR3_MARK:
            source.seek(data_offset + add_size)
            continue

        if block_type == _RAR3_MAIN:
            pos += 6
            if flags & _RAR3_MAIN_ENCRYPTVER:
                pos += 1
            crc_pos = pos
            is_solid = bool(flags & _RAR3_MAIN_SOLID)
            is_volume = bool(flags & _RAR3_MAIN_VOLUME)
            if flags & _RAR3_MAIN_PASSWORD:
                has_header_encryption = True
                if password is None:
                    raise EncryptionError(
                        "RAR archive has encrypted headers but no password was provided"
                    )
            # Skip CRC of main for now after optional comment subblocks.
            if flags & _RAR3_MAIN_COMMENT:
                # Old-style embedded comment subblocks — skip parsing compressed comments.
                pass
            calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
            if header_crc != calc:
                raise CorruptionError(
                    f"RAR3 MAIN header CRC mismatch: expected {header_crc:#x}, got {calc:#x}"
                )
            source.seek(data_offset + add_size)
            continue

        if block_type == _RAR3_ENDARC:
            calc = _crc32(hdata[2:header_size]) & 0xFFFF
            if header_crc != calc:
                raise CorruptionError("RAR3 ENDARC header CRC mismatch")
            needs_next_volume = bool(flags & _RAR3_ENDARC_NEXT_VOLUME)
            break

        if block_type in (_RAR3_FILE, _RAR3_SUB):
            # FILE header re-reads pack_size as first field when LONG_BLOCK was set.
            file_pos = pos - 4 if (flags & _RAR3_LONG_BLOCK) else pos
            member, crc_pos = _parse_rar3_file_header(
                hdata,
                file_pos,
                flags=flags,
                header_offset=header_offset,
                header_size=header_size,
                data_offset=data_offset,
                volume_index=volume_index,
                is_service=(block_type == _RAR3_SUB),
            )
            calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
            if header_crc != calc:
                raise CorruptionError(
                    f"RAR3 FILE header CRC mismatch: expected {header_crc:#x}, got {calc:#x}"
                )

            if block_type == _RAR3_FILE:
                # RAR 1.5 / 2.x use the same block layout as RAR3 for headers we
                # care about; member data is always left to RARLAB ``unrar``.
                # Do not reject extract_version ≤ 20 — that also false-positives
                # modern RAR3 archives whose stored/small members advertise
                # unp_ver=20.
                if not (flags & _RAR3_FILE_VERSION):
                    if member.split_before:
                        if members:
                            _merge_split_member(members[-1], member)
                        else:
                            # Continuation without a prior part in this volume.
                            members.append(member)
                    else:
                        members.append(member)
                    if member.split_after:
                        needs_next_volume = True
            elif (
                block_type == _RAR3_SUB
                and member.filename == "CMT"
                and member.compress_type == _RAR3_M0
                and not member.is_encrypted
                and not member.split_before
                and not member.split_after
                and member.compress_size > 0
            ):
                source.seek(data_offset)
                raw = _require_exact(source, member.compress_size, "RAR3 comment")
                cmt = _decode_name(raw.split(b"\0", 1)[0])
                if member.file_solid and members:
                    members[-1].comment = cmt
                else:
                    comment = cmt

            source.seek(data_offset + add_size)
            continue

        # Unknown / skippable block
        source.seek(data_offset + add_size)

    return RarArchive(
        version=4,
        is_solid=is_solid,
        has_header_encryption=has_header_encryption,
        comment=comment,
        members=members,
        sfx_offset=sfx_offset,
        is_volume=is_volume,
        needs_next_volume=needs_next_volume,
    )


def _rar3_decrypt_header(
    source: BinaryIO, state: _Rar3EncState
) -> _HeaderDecryptStream:
    salt = _require_exact(source, 8, "RAR3 header salt")
    if (
        state.last_salt == salt
        and state.last_key is not None
        and state.last_iv is not None
    ):
        key, iv = state.last_key, state.last_iv
    else:
        key, iv = _rar3_s2k(state.password, salt)
        state.last_salt = salt
        state.last_key = key
        state.last_iv = iv
    return _HeaderDecryptStream(source, key, iv)


def _parse_rar3_file_header(
    hdata: bytes,
    pos: int,
    *,
    flags: int,
    header_offset: int,
    header_size: int,
    data_offset: int,
    volume_index: int,
    is_service: bool,
) -> tuple[RarMemberInfo, int]:
    if pos + _S_FILE_HDR.size > len(hdata):
        raise CorruptionError("Truncated RAR3 file header")
    fld = _S_FILE_HDR.unpack_from(hdata, pos)
    pos += _S_FILE_HDR.size

    compress_size = fld[0]
    file_size = fld[1]
    host_os = fld[2]
    crc32 = fld[3]
    dos_stamp = fld[4]
    extract_version = fld[5]
    compress_type = fld[6]
    name_size = fld[7]
    mode = fld[8]

    mtime: datetime | None = _parse_dos_time(dos_stamp)

    if flags & _RAR3_FILE_LARGE:
        h1, pos = _load_le32(hdata, pos)
        h2, pos = _load_le32(hdata, pos)
        compress_size |= h1 << 32
        file_size |= h2 << 32

    name, pos = _load_bytes(hdata, name_size, pos)
    orig_filename: bytes | None
    if flags & _RAR3_FILE_UNICODE and b"\0" in name:
        nul = name.find(b"\0")
        orig_filename = name[:nul]
        u = _UnicodeFilename(orig_filename, name[nul + 1 :])
        filename = u.decode()
        if u.failed:
            filename = _decode_name(orig_filename)
    elif flags & _RAR3_FILE_UNICODE:
        orig_filename = name
        filename = name.decode("utf8", "replace")
    else:
        orig_filename = name
        filename = _decode_name(name)

    filename = filename.replace("\\", "/").rstrip("/")
    is_directory = (flags & _RAR3_FILE_DIRECTORY) == _RAR3_FILE_DIRECTORY
    is_symlink = (
        not is_service
        and host_os == _RAR3_OS_UNIX
        and mode is not None
        and (mode & 0xF000) == 0xA000
    )
    if is_directory:
        filename = filename + "/"

    if flags & _RAR3_FILE_SALT:
        _salt, pos = _load_bytes(hdata, 8, pos)

    if flags & _RAR3_FILE_EXTTIME:
        mtime_holder: list[datetime | None] = [mtime]
        pos = _parse_rar3_ext_time(hdata, pos, mtime_holder)
        mtime = mtime_holder[0]
    # else: keep DOS mtime (spec: RAR4 naive wall-clock)

    # CRC covers through the file-header fields; old comment subblocks (if any) follow.
    crc_pos = pos if not is_service else header_size

    member = RarMemberInfo(
        filename=filename,
        orig_filename=orig_filename,
        file_size=file_size,
        compress_size=compress_size,
        compress_type=compress_type,
        crc32=crc32,
        blake2sp_hash=None,
        mtime=None if is_service else mtime,
        mode=mode,
        host_os=host_os,
        flags=flags,
        file_redir=None,
        file_encryption=None,
        header_offset=header_offset,
        header_size=header_size,
        data_offset=data_offset,
        extract_version=extract_version,
        file_solid=bool(flags & _RAR3_FILE_SOLID),
        is_directory=is_directory and not is_symlink,
        is_symlink=is_symlink,
        is_hardlink_or_copy=False,
        is_encrypted=bool(flags & _RAR3_FILE_PASSWORD),
        volume_index=volume_index,
        split_before=bool(flags & _RAR3_FILE_SPLIT_BEFORE),
        split_after=bool(flags & _RAR3_FILE_SPLIT_AFTER),
    )
    return member, crc_pos


def _parse_rar3_ext_time(
    data: bytes, pos: int, mtime_holder: list[datetime | None]
) -> int:
    """Parse RAR3 extended time; may refine ``mtime_holder[0]``."""
    flags = 0
    if pos + 2 <= len(data):
        flags = _S_SHORT.unpack_from(data, pos)[0]
        pos += 2

    mtime, pos = _parse_rar3_xtime(flags >> 12, data, pos, mtime_holder[0])
    _, pos = _parse_rar3_xtime(flags >> 8, data, pos, None)
    _, pos = _parse_rar3_xtime(flags >> 4, data, pos, None)
    _, pos = _parse_rar3_xtime(flags, data, pos, None)
    if mtime is not None:
        mtime_holder[0] = mtime
    return pos


def _parse_rar3_xtime(
    flag: int,
    data: bytes,
    pos: int,
    basetime: datetime | None,
) -> tuple[datetime | None, int]:
    if not (flag & 8):
        return None, pos
    if basetime is None:
        if pos + 4 > len(data):
            return None, pos
        stamp, pos = _load_le32(data, pos)
        basetime = _parse_dos_time(stamp)

    rem = 0
    cnt = flag & 3
    for _ in range(cnt):
        if pos >= len(data):
            break
        b, pos = _load_byte(data, pos)
        rem = (b << 16) | (rem >> 8)

    if flag & 4 and basetime.second < 59:
        basetime = basetime.replace(second=basetime.second + 1)

    # Convert 100ns units to microseconds (rarfile uses nsdatetime; we keep µs).
    usec = (rem * 100) // 1000
    try:
        return basetime.replace(microsecond=min(usec, 999999)), pos
    except ValueError:
        return basetime, pos


@dataclass
class _Rar5HdrEnc:
    algo: int
    flags: int
    kdf_count: int
    salt: bytes
    check_value: bytes | None
    cached_key: bytes | None = None


def _parse_rar5(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    sfx_offset: int,
    volume_index: int = 0,
) -> RarArchive:
    _require_exact(source, len(RAR5_ID), "RAR5 signature")

    is_solid = False
    is_volume = False
    has_header_encryption = False
    comment: str | None = None
    members: list[RarMemberInfo] = []
    hdr_enc: _Rar5HdrEnc | None = None
    needs_next_volume = False

    while True:
        header_fd: _Readable = source
        if hdr_enc is not None:
            has_header_encryption = True
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            try:
                header_fd = _rar5_decrypt_header(source, hdr_enc, password)
            except PackageNotInstalledError:
                raise
            except EncryptionError:
                raise
            except Exception as exc:
                raise EncryptionError(f"Failed to decrypt RAR5 headers: {exc}") from exc

        parsed = _read_rar5_block(header_fd)
        if parsed is None:
            break
        (
            block_type,
            block_flags,
            hdata,
            pos,
            header_offset,
            header_size,
            data_offset,
            add_size,
            extra_size,
        ) = parsed

        if block_type == _RAR5_MAIN:
            main_flags, pos = _load_vint(hdata, pos)
            if main_flags & _RAR5_MAIN_HAS_VOLNR:
                volnr, pos = _load_vint(hdata, pos)
                # RAR5: first volume omits the field (implicit 0); later volumes
                # store 1 for the second volume, 2 for the third, …
                if volume_index == 0 and volnr != 0:
                    raise UnsupportedFeatureError(
                        "Need first volume of multi-volume RAR archive"
                    )
                if volume_index > 0 and volnr != volume_index:
                    raise TruncatedError(
                        f"Out-of-order RAR volume: expected volume index "
                        f"{volume_index}, got {volnr}"
                    )
            elif volume_index > 0:
                raise TruncatedError(
                    f"Out-of-order RAR volume: expected volume index {volume_index}, "
                    f"got first-volume header"
                )
            is_solid = bool(main_flags & _RAR5_MAIN_SOLID)
            is_volume = bool(main_flags & _RAR5_MAIN_ISVOL)
            source.seek(data_offset + add_size)
            continue

        if block_type == _RAR5_ENCRYPTION:
            algo, pos = _load_vint(hdata, pos)
            enc_flags, pos = _load_vint(hdata, pos)
            kdf_count, pos = _load_byte(hdata, pos)
            salt, pos = _load_bytes(hdata, 16, pos)
            check_value = None
            if enc_flags & _RAR5_ENC_HAS_CHECKVAL:
                check_value, pos = _load_bytes(hdata, 12, pos)
            if algo != _RAR5_XENC_AES256:
                raise UnsupportedFeatureError(
                    f"Unsupported RAR5 header encryption cipher: {algo}"
                )
            if check_value is not None and password is not None:
                _check_rar5_password(check_value, kdf_count, salt, password)
            hdr_enc = _Rar5HdrEnc(
                algo=algo,
                flags=enc_flags,
                kdf_count=kdf_count,
                salt=salt,
                check_value=check_value,
            )
            has_header_encryption = True
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            source.seek(data_offset + add_size)
            continue

        if block_type == _RAR5_ENDARC:
            endarc_flags, _ = _load_vint(hdata, pos)
            needs_next_volume = bool(endarc_flags & _RAR5_ENDARC_NEXT_VOLUME)
            break

        if block_type in (_RAR5_FILE, _RAR5_SERVICE):
            member, skip_version = _parse_rar5_file_block(
                hdata,
                pos,
                block_flags=block_flags,
                extra_size=extra_size,
                header_offset=header_offset,
                header_size=header_size,
                data_offset=data_offset,
                add_size=add_size,
                volume_index=volume_index,
            )
            if block_type == _RAR5_FILE and not skip_version:
                if member.split_before:
                    if members:
                        _merge_split_member(members[-1], member)
                    else:
                        members.append(member)
                else:
                    members.append(member)
                if member.split_after:
                    needs_next_volume = True
            elif (
                block_type == _RAR5_SERVICE
                and member.filename == "CMT"
                and member.compress_type == _RAR3_M0
                and not member.split_before
                and not member.split_after
                and member.compress_size > 0
                and not member.is_encrypted
            ):
                source.seek(data_offset)
                raw = _require_exact(source, member.file_size, "RAR5 comment")
                comment = raw.split(b"\0", 1)[0].decode("utf8", "replace")
            source.seek(data_offset + add_size)
            continue

        # Unknown block — skip data area.
        source.seek(data_offset + add_size)

    return RarArchive(
        version=5,
        is_solid=is_solid,
        has_header_encryption=has_header_encryption,
        comment=comment,
        members=members,
        sfx_offset=sfx_offset,
        is_volume=is_volume,
        needs_next_volume=needs_next_volume,
    )


def _read_rar5_block(
    fd: _Readable,
) -> tuple[int, int, bytes, int, int, int, int, int, int] | None:
    """Read one RAR5 block.

    Returns
    ``(type, flags, hdata, pos_after_common, header_offset, header_size,
    data_offset, add_size, extra_size)`` or ``None`` at EOF.
    """
    header_offset = fd.tell()
    preload = 4 + 1
    start_bytes = fd.read(preload)
    if not start_bytes:
        return None
    if len(start_bytes) < preload:
        raise CorruptionError("Unexpected EOF while reading RAR5 header")
    while start_bytes[-1] & 0x80:
        b = fd.read(1)
        if not b:
            raise CorruptionError("Unexpected EOF while reading RAR5 header size")
        start_bytes += b
    header_crc, pos = _load_le32(start_bytes, 0)
    hdrlen, pos = _load_vint(start_bytes, pos)
    if hdrlen > _RAR5_MAX_HEADER:
        raise CorruptionError(f"RAR5 header too large: {hdrlen}")
    header_size = pos + hdrlen
    hdata = start_bytes + fd.read(header_size - len(start_bytes))
    if len(hdata) != header_size:
        raise CorruptionError("Unexpected EOF while reading RAR5 header body")
    data_offset = fd.tell()

    if header_crc != _crc32(memoryview(hdata)[4:]):
        raise CorruptionError(f"RAR5 header CRC mismatch at offset {header_offset}")

    block_type, pos = _load_vint(hdata, pos)
    block_flags, pos = _load_vint(hdata, pos)
    extra_size = 0
    add_size = 0
    if block_flags & _RAR5_FLAG_EXTRA:
        extra_size, pos = _load_vint(hdata, pos)
    if block_flags & _RAR5_FLAG_DATA:
        add_size, pos = _load_vint(hdata, pos)
    return (
        block_type,
        block_flags,
        hdata,
        pos,
        header_offset,
        header_size,
        data_offset,
        add_size,
        extra_size,
    )


def _rar5_decrypt_header(
    source: BinaryIO, hdr_enc: _Rar5HdrEnc, password: str | bytes
) -> _HeaderDecryptStream:
    if hdr_enc.kdf_count > _RAR_MAX_KDF_SHIFT:
        raise CorruptionError(f"RAR5 kdf_count too large: {hdr_enc.kdf_count}")
    if hdr_enc.cached_key is None:
        hdr_enc.cached_key = _rar5_s2k(password, hdr_enc.salt, 1 << hdr_enc.kdf_count)
    iv = _require_exact(source, 16, "RAR5 header IV")
    return _HeaderDecryptStream(source, hdr_enc.cached_key, iv)


def _check_rar5_password(
    check_value: bytes, kdf_count_shift: int, salt: bytes, password: str | bytes
) -> None:
    if len(check_value) != 12:
        return
    if kdf_count_shift > _RAR_MAX_KDF_SHIFT:
        raise CorruptionError(f"RAR5 kdf_count too large: {kdf_count_shift}")
    hdr_check = check_value[:8]
    hdr_sum = check_value[8:]
    if hashlib.sha256(hdr_check).digest()[:4] != hdr_sum:
        return
    kdf_count = (1 << kdf_count_shift) + 32
    pwd_hash = _rar5_s2k(password, salt, kdf_count)
    pwd_check = bytearray(8)
    for i, v in enumerate(pwd_hash):
        pwd_check[i & 7] ^= v
    if bytes(pwd_check) != hdr_check:
        raise EncryptionError("Wrong password for RAR5 header encryption")


def _parse_rar5_file_block(
    hdata: bytes,
    pos: int,
    *,
    block_flags: int,
    extra_size: int,
    header_offset: int,
    header_size: int,
    data_offset: int,
    add_size: int,
    volume_index: int,
) -> tuple[RarMemberInfo, bool]:
    file_flags, pos = _load_vint(hdata, pos)
    file_size, pos = _load_vint(hdata, pos)
    mode, pos = _load_vint(hdata, pos)

    mtime: datetime | None = None
    crc32: int | None = None
    if file_flags & _RAR5_FILE_HAS_MTIME:
        mtime, pos = _load_unixtime(hdata, pos)
    if file_flags & _RAR5_FILE_HAS_CRC32:
        crc32, pos = _load_le32(hdata, pos)

    compress_info, pos = _load_vint(hdata, pos)
    host_os_raw, pos = _load_vint(hdata, pos)
    orig_filename, pos = _load_vstr(hdata, pos)
    filename = orig_filename.decode("utf8", "replace").rstrip("/")

    host_os = 2 if host_os_raw == _RAR5_OS_WINDOWS else 3  # RAR_OS_WIN32 / UNIX
    compress_type = _RAR3_M0 + ((compress_info >> 7) & 7)
    file_solid = bool(compress_info & _RAR5_COMPR_SOLID)
    is_directory = bool(file_flags & _RAR5_FILE_ISDIR)
    split_before = bool(block_flags & _RAR5_FLAG_SPLIT_BEFORE)
    split_after = bool(block_flags & _RAR5_FLAG_SPLIT_AFTER)

    blake2sp_hash: bytes | None = None
    file_redir: tuple[int, int, str] | None = None
    file_encryption: RarEncryptionInfo | None = None
    skip_version = False
    flags = 0

    if extra_size:
        # Walk extras until near end (allow 1 byte of padding like rarfile).
        while pos < len(hdata) - 1:
            try:
                xsize, pos = _load_vint(hdata, pos)
            except CorruptionError:
                break
            if xsize < 0 or pos + xsize > len(hdata):
                break
            xdata, pos = _load_bytes(hdata, xsize, pos)
            xtype, xpos = _load_vint(xdata, 0)
            if xtype == _RAR5_XFILE_TIME:
                mtime = _parse_rar5_xtime(xdata, xpos, mtime)
            elif xtype == _RAR5_XFILE_ENCRYPTION:
                file_encryption = _parse_rar5_file_encryption(xdata, xpos)
                flags |= _RAR3_FILE_PASSWORD
            elif xtype == _RAR5_XFILE_HASH:
                hash_type, xpos = _load_vint(xdata, xpos)
                if hash_type == _RAR5_XHASH_BLAKE2SP:
                    blake2sp_hash, xpos = _load_bytes(xdata, 32, xpos)
            elif xtype == _RAR5_XFILE_REDIR:
                redir_type, xpos = _load_vint(xdata, xpos)
                redir_flags, xpos = _load_vint(xdata, xpos)
                redir_name, xpos = _load_vstr(xdata, xpos)
                file_redir = (
                    redir_type,
                    redir_flags,
                    redir_name.decode("utf8", "replace"),
                )
            elif xtype == _RAR5_XFILE_VERSION:
                _vflags, xpos = _load_vint(xdata, xpos)
                _version, xpos = _load_vint(xdata, xpos)
                skip_version = True
            # OWNER / SERVICE / unknown: ignore

    is_symlink = False
    is_hardlink_or_copy = False
    if file_redir is not None:
        rtype = file_redir[0]
        if rtype in (
            _RAR5_XREDIR_UNIX_SYMLINK,
            _RAR5_XREDIR_WINDOWS_SYMLINK,
            _RAR5_XREDIR_WINDOWS_JUNCTION,
        ):
            is_symlink = True
        elif rtype in (_RAR5_XREDIR_HARD_LINK, _RAR5_XREDIR_FILE_COPY):
            is_hardlink_or_copy = True

    if is_directory and not is_symlink:
        filename = filename + "/"

    member = RarMemberInfo(
        filename=filename,
        orig_filename=orig_filename,
        file_size=file_size,
        compress_size=add_size,
        compress_type=compress_type,
        crc32=crc32,
        blake2sp_hash=blake2sp_hash,
        mtime=mtime,
        mode=mode,
        host_os=host_os,
        flags=flags,
        file_redir=file_redir,
        file_encryption=file_encryption,
        header_offset=header_offset,
        header_size=header_size,
        data_offset=data_offset,
        extract_version=50,
        file_solid=file_solid,
        is_directory=is_directory and not is_symlink,
        is_symlink=is_symlink,
        is_hardlink_or_copy=is_hardlink_or_copy,
        is_encrypted=file_encryption is not None,
        volume_index=volume_index,
        split_before=split_before,
        split_after=split_after,
    )
    return member, skip_version


def _parse_rar5_xtime(
    xdata: bytes, pos: int, current: datetime | None
) -> datetime | None:
    tflags, pos = _load_vint(xdata, pos)
    ldr = _load_windowstime
    if tflags & _RAR5_XTIME_UNIXTIME:
        ldr = _load_unixtime
    mtime = current
    if tflags & _RAR5_XTIME_HAS_MTIME:
        mtime, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_HAS_CTIME:
        _, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_HAS_ATIME:
        _, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_UNIXTIME_NS:
        if tflags & _RAR5_XTIME_HAS_MTIME and mtime is not None:
            nsec, pos = _load_le32(xdata, pos)
            mtime = mtime.replace(microsecond=min(nsec // 1000, 999999))
        if tflags & _RAR5_XTIME_HAS_CTIME:
            _, pos = _load_le32(xdata, pos)
        if tflags & _RAR5_XTIME_HAS_ATIME:
            _, pos = _load_le32(xdata, pos)
    return mtime


def _parse_rar5_file_encryption(xdata: bytes, pos: int) -> RarEncryptionInfo:
    algo, pos = _load_vint(xdata, pos)
    flags, pos = _load_vint(xdata, pos)
    kdf_count, pos = _load_byte(xdata, pos)
    salt, pos = _load_bytes(xdata, 16, pos)
    iv, pos = _load_bytes(xdata, 16, pos)
    check_value = None
    if flags & _RAR5_XENC_CHECKVAL:
        check_value, pos = _load_bytes(xdata, 12, pos)
    return RarEncryptionInfo(
        algo=algo,
        flags=flags,
        kdf_count=kdf_count,
        salt=salt,
        iv=iv,
        check_value=check_value,
    )
