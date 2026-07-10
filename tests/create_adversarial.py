"""Adversarial *string* corpus: hostile bytes in member names, link targets, comments.

Real writer libraries (`zipfile`, `tarfile`) refuse to emit the worst inputs — a lone
surrogate name raises `UnicodeEncodeError` at write time — so this corpus cannot be built
declaratively like ``tests/sample_archives.py``. Instead it follows the byte-splice model
the maintainer approved (and that the ISO directory-cycle test already uses): commit a
clean **base** archive for reproducibility, then splice an equal-length hostile token into
a specific field. Equal length keeps every offset (and, for ZIP, the CRC — the name is not
covered by it) valid; TAR's header checksum is recomputed after the splice.

The base archives embed distinctive ASCII **placeholder tokens** (`NAME_ZZZZ…`, etc.) so a
splice locates its field by searching for the token rather than a hard-coded offset — if a
base is regenerated and offsets move, the splices still land (the maintainer's ISO
concern). ``python -m tests.create_adversarial`` (re)writes the committed base fixtures.

The committed fixtures are the *clean* base archives; the hostile variants are produced in
memory at test time (``adversarial_archives()``) and never written to disk, so no file on
the repo tree carries a hostile name.

Attack categories exercised (see ``testing-contract`` "Unicode bombs" row): NUL bytes,
lone surrogates (WTF-8), invalid UTF-8, RTL override, overlong UTF-8 — in the member name,
the symlink target, and the member/file comment; plus a UTF-8-flag-lie (ZIP name flagged
UTF-8 but not valid UTF-8).
"""

from __future__ import annotations

import io
import stat
import struct
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "adversarial"

# Distinctive, equal-per-field-length ASCII placeholders spliced over at test time.
_NAME = b"NAME_ZZZZZZZZ.txt"  # 17 bytes
_LINK = b"LINKTARGET_ZZZZZ"  # 16 bytes
_COMMENT = b"COMMENT_ZZZZZZZZ"  # 16 bytes


# --- hostile tokens ---------------------------------------------------------------

# Each hostile byte sequence, padded with a benign filler to the field's placeholder
# length so the splice is length-preserving.
_HOSTILE: dict[str, bytes] = {
    "nul": b"\x00",
    "lone_surrogate": b"\xed\xa0\x80",  # WTF-8 encoding of U+D800 (invalid UTF-8)
    "invalid_utf8": b"\xff\xfe",
    "rtl_override": b"\xe2\x80\xae",  # U+202E RIGHT-TO-LEFT OVERRIDE (valid UTF-8)
    "overlong": b"\xc0\xaf",  # overlong encoding of "/" (invalid UTF-8)
}


def _padded(token: bytes, length: int) -> bytes:
    body = token + b"x" * (length - len(token))
    assert len(body) == length, f"{token!r} does not fit in {length} bytes"
    return body


# --- base archives (committed) ----------------------------------------------------


def build_base_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(_NAME.decode())
        # Pin create_system to Unix on every entry: zipfile otherwise stamps the host OS
        # into "version made by" (0 on Windows, 3 elsewhere), which would make the base
        # bytes — and thus the committed fixture — platform-dependent.
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.comment = _COMMENT
        zf.writestr(info, b"payload for the adversarial name member\n")
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, _LINK)
    return buf.getvalue()


def build_base_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo(_NAME.decode())
        payload = b"payload for the adversarial name member\n"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = _LINK.decode()
        tf.addfile(link)
    return buf.getvalue()


# --- splicing ---------------------------------------------------------------------


def _zip_splice(base: bytes, token: bytes, replacement: bytes) -> bytes:
    """Replace every occurrence of ``token`` (local + central copies) in a ZIP.

    Length-preserving: names are not covered by the CRC and the length fields are
    unchanged, so no header repair is needed.
    """
    assert len(token) == len(replacement)
    assert token in base
    return base.replace(token, replacement)


def _tar_recompute_checksum(data: bytearray, block_start: int) -> None:
    header = data[block_start : block_start + 512]
    header[148:156] = b" " * 8  # checksum field is spaces while summing
    chksum = sum(header)
    data[block_start + 148 : block_start + 156] = b"%06o\x00 " % chksum


def _tar_splice(base: bytes, token: bytes, replacement: bytes) -> bytes:
    """Replace ``token`` in a TAR field and repair the containing header's checksum."""
    assert len(token) == len(replacement)
    idx = base.index(token)
    data = bytearray(base)
    data[idx : idx + len(token)] = replacement
    _tar_recompute_checksum(data, (idx // 512) * 512)
    return bytes(data)


def _flag_utf8(base: bytes, name_token: bytes) -> bytes:
    """Set the UTF-8 name flag (general-purpose bit 11) on the ZIP entry named by token.

    Patches the two-byte flags field in both the local file header (token at +30) and the
    central directory header (token at +46) that precede ``name_token``.
    """
    data = bytearray(base)
    for header_len, sig in ((30, b"PK\x03\x04"), (46, b"PK\x01\x02")):
        pos = data.find(name_token)
        while pos != -1:
            start = pos - header_len
            if start >= 0 and bytes(data[start : start + 4]) == sig:
                flag_off = start + (6 if header_len == 30 else 8)
                flags = struct.unpack_from("<H", data, flag_off)[0] | (1 << 11)
                struct.pack_into("<H", data, flag_off, flags)
            pos = data.find(name_token, pos + 1)
    return bytes(data)


# --- corpus manifest --------------------------------------------------------------


@dataclass(frozen=True)
class Adversarial:
    id: str
    fmt: str  # "zip" | "tar"
    build: Callable[[bytes], bytes]  # (base_bytes) -> spliced_bytes
    # Expected outcome category for the sweep:
    #   "reject"  -> extraction must raise a FilterRejectionError (typed rejection)
    #   "safe"    -> open/list/read/extract complete or raise some other ArchiveyError,
    #                never a raw exception, never a file outside dest
    outcome: str


def _name_variant(fmt: str, key: str, outcome: str) -> Adversarial:
    splice = _zip_splice if fmt == "zip" else _tar_splice
    repl = _padded(_HOSTILE[key], len(_NAME))
    return Adversarial(
        f"{fmt}-name-{key}", fmt, lambda b, r=repl: splice(b, _NAME, r), outcome
    )


CORPUS: tuple[Adversarial, ...] = (
    # NUL in a member name is a hard rejection on every platform.
    _name_variant("zip", "nul", "reject"),
    _name_variant("tar", "nul", "reject"),
    # The rest must be handled safely (decoded via fallback, or a typed error) — never a
    # raw crash. High bytes decode via cp437 for un-flagged ZIP names.
    _name_variant("zip", "lone_surrogate", "safe"),
    _name_variant("zip", "invalid_utf8", "safe"),
    _name_variant("zip", "rtl_override", "safe"),
    _name_variant("zip", "overlong", "safe"),
    _name_variant("tar", "invalid_utf8", "safe"),
    _name_variant("tar", "rtl_override", "safe"),
    # UTF-8-flag-lie: the ZIP name is flagged UTF-8 but is not valid UTF-8.
    Adversarial(
        "zip-name-utf8-flag-lie",
        "zip",
        lambda b: _flag_utf8(
            _zip_splice(b, _NAME, _padded(_HOSTILE["invalid_utf8"], len(_NAME))), _NAME
        ),
        "safe",
    ),
    # Hostile symlink target: NUL, and a traversal-via-invalid-bytes attempt.
    Adversarial(
        "zip-link-nul",
        "zip",
        lambda b: _zip_splice(b, _LINK, _padded(_HOSTILE["nul"], len(_LINK))),
        "safe",  # NUL target: rejected at link creation or left unresolved — never a crash
    ),
    Adversarial(
        "tar-link-invalid_utf8",
        "tar",
        lambda b: _tar_splice(b, _LINK, _padded(_HOSTILE["invalid_utf8"], len(_LINK))),
        "safe",
    ),
    # Hostile comment (ZIP): comment decoding must be lossy-but-safe.
    Adversarial(
        "zip-comment-invalid_utf8",
        "zip",
        lambda b: _zip_splice(
            b, _COMMENT, _padded(_HOSTILE["invalid_utf8"], len(_COMMENT))
        ),
        "safe",
    ),
)


def adversarial_archives() -> "list[tuple[Adversarial, bytes]]":
    """Every corpus entry as ``(entry, spliced_archive_bytes)``, built in memory."""
    bases = {"zip": _read_base("base.zip"), "tar": _read_base("base.tar")}
    return [(entry, entry.build(bases[entry.fmt])) for entry in CORPUS]


def _read_base(name: str) -> bytes:
    path = FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"missing base fixture {path}; run `python -m tests.create_adversarial`"
        )
    return path.read_bytes()


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "base.zip").write_bytes(build_base_zip())
    (FIXTURE_DIR / "base.tar").write_bytes(build_base_tar())
    print(f"wrote base.zip and base.tar to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
