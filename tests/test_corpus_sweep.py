"""The corpus conformance sweep (``testing-contract``: corpus conformance sweep).

One parametrized driver over (corpus entry × format): every archive the declarative
corpus describes, in every format it is built in, must open, list members matching the
declared expectations, read back the declared contents (following links per the
link-resolution contract), and extract safely — with adversarial members rejected and
encrypted members unreadable without their password. Formats whose reader is not
available (missing optional dependency, or the RAR reader before Phase 7) are
skipped via the registry's availability guard, so enabling a format activates its
entries with no test changes.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from archivey import (
    ArchiveyError,
    EncryptionError,
    ExtractionStatus,
    FormatSupport,
    MemberStreams,
    MemberType,
    OnError,
    format_availability,
    open_archive,
)
from archivey.types import HashAlgorithm
from tests.conftest import _has_zstd_backend
from tests.sample_archives import (
    BUILDER_BINARIES,
    BUILDER_PACKAGES,
    CORPUS,
    FORMAT_KEYS,
    CorpusEntry,
    Member,
    corpus_archive_path,
)

# Formats where the reader reports Unix permission bits for our generated archives.
_MODE_FORMATS = {
    "tar",
    "tar.gz",
    "tar.bz2",
    "tar.xz",
    "tar.zst",
    "tar.lz4",
    "tar.lz",
    "tar.zz",
    "tar.br",
    "zip",
}
# Single-member formats: the member name is inferred from the archive filename, so the
# listing check differs (see format-single-file-compressors).
_SINGLE_FILE_KEYS = {"gz", "gz-meta", "bz2", "xz", "zst", "lz4", "lz", "zz", "br"}

_PARAMS = [
    pytest.param(entry, key, id=f"{entry.id}-{key}")
    for entry in CORPUS
    for key in entry.formats
]


def _skip_unless_runnable(entry: CorpusEntry, key: str) -> None:
    availability = format_availability(FORMAT_KEYS[key])
    if availability.support is FormatSupport.NONE:
        pytest.skip(
            f"format {key!r} not readable here: {availability.missing or 'no backend'}"
        )
    for package in BUILDER_PACKAGES.get(key, ()):
        if package == "_zstd_backend":
            if not _has_zstd_backend():
                pytest.skip("no zstd backend to build with")
        elif importlib.util.find_spec(package) is None:
            pytest.skip(f"builder needs package {package!r}")
    entry_binaries = entry.requires_binaries
    if key == "7z":
        # Encrypted ZIP corpus entries need the 7z CLI builder, but encrypted 7z entries
        # are written directly by py7zr with a single archive password.
        entry_binaries = tuple(b for b in entry_binaries if b != "7z")
    for binary in (*BUILDER_BINARIES.get(key, ()), *entry_binaries):
        if shutil.which(binary) is None:
            pytest.skip(f"builder needs binary {binary!r}")
    if (
        os.name == "nt"
        and any(m.type is MemberType.SYMLINK for m in entry.members)
        and key in ("dir", "7z")
    ):
        pytest.skip("creating symlinks on Windows needs privileges")


def _expected_occurrences(entry: CorpusEntry) -> dict[str, list[Member]]:
    by_name: dict[str, list[Member]] = {}
    for m in entry.members:
        by_name.setdefault(m.name, []).append(m)
    return by_name


def _check_listing(ar, entry: CorpusEntry, key: str) -> None:
    actual_by_name: dict[str, list] = {}
    for member in ar.members():
        actual_by_name.setdefault(member.name, []).append(member)

    expected = _expected_occurrences(entry)
    for name, expected_list in expected.items():
        actual_list = actual_by_name.get(name)
        assert actual_list is not None, f"missing member {name!r}"
        assert len(actual_list) == len(expected_list), f"occurrence count for {name!r}"
        for exp, act in zip(expected_list, actual_list):
            assert act.type is exp.type, f"type of {name!r}"
            if exp.type is MemberType.FILE and not exp.password:
                assert act.size == len(exp.contents), f"size of {name!r}"
            if exp.link_target is not None and key != "iso":
                assert act.link_target == exp.link_target, f"link_target of {name!r}"
            if exp.mode is not None and key in _MODE_FORMATS:
                assert act.mode == exp.mode, f"mode of {name!r}"
            if exp.uid is not None and key.startswith("tar"):
                assert act.uid == exp.uid, f"uid of {name!r}"
            if exp.comment is not None and key == "zip":
                assert act.comment == exp.comment, f"comment of {name!r}"
            _assert_stored_digest_parity(act, key)

    # Unexpected extras: only implicit parent DIRECTORY members are tolerated (the
    # directory/ISO backends materialize parents the shape left implicit).
    for name, actual_list in actual_by_name.items():
        if name not in expected:
            assert all(m.type is MemberType.DIRECTORY for m in actual_list), (
                f"unexpected non-directory member {name!r}"
            )

    if entry.archive_comment is not None and key == "zip":
        assert ar.info.comment == entry.archive_comment


def _assert_stored_digest_parity(member, key: str) -> None:
    """Assert documented stored-digest keys are present/absent (testing-contract)."""
    keys = set(member.hashes)
    digest_keys = keys & {
        HashAlgorithm.CRC32,
        HashAlgorithm.BLAKE2SP,
        HashAlgorithm.ADLER32,
    }
    if key == "zip":
        if member.type in (MemberType.FILE, MemberType.SYMLINK):
            # AE-2 (WinZip AES) stores CRC as 0 and relies on the HMAC — no crc32 digest.
            if member.extra.get("zip.aes_vendor_version") == 2:
                assert HashAlgorithm.CRC32 not in keys, (
                    f"zip AE-2 {member.name!r} should not surface crc32"
                )
            else:
                assert HashAlgorithm.CRC32 in keys, f"zip {member.name!r} missing crc32"
        else:
            assert HashAlgorithm.CRC32 not in keys, (
                f"zip {member.name!r} unexpected crc32"
            )
        return
    if key == "7z":
        if member.type is MemberType.FILE:
            assert HashAlgorithm.CRC32 in keys, f"7z {member.name!r} missing crc32"
        elif member.type is MemberType.DIRECTORY:
            assert HashAlgorithm.CRC32 not in keys, (
                f"7z {member.name!r} unexpected crc32"
            )
        # SYMLINK may carry a CRC of the stored link payload; do not require or forbid.
        return
    if key == "rar":
        if member.type is MemberType.FILE:
            # RAR5 may store Blake2sp instead of (or in addition to) CRC32.
            assert digest_keys, f"rar FILE {member.name!r} missing stored digest"
            if HashAlgorithm.BLAKE2SP in keys and HashAlgorithm.CRC32 not in keys:
                return
            assert HashAlgorithm.CRC32 in keys or HashAlgorithm.BLAKE2SP in keys
        else:
            assert not digest_keys, (
                f"rar {member.name!r} unexpected digests {digest_keys}"
            )
        return
    # TAR, directory, ISO, compressed-TAR: no cheap whole-member stored digest.
    assert not digest_keys, f"{key} {member.name!r} unexpected digests {digest_keys}"


def _check_reads(ar, entry: CorpusEntry) -> None:
    # Read every occurrence via its own member object (duplicate names must resolve to
    # their own data, and links must follow to the declared terminal contents).
    actual_by_name: dict[str, list] = {}
    for member in ar.members():
        actual_by_name.setdefault(member.name, []).append(member)
    for name, expected_list in _expected_occurrences(entry).items():
        for exp, act in zip(expected_list, actual_by_name[name]):
            if exp.expect_read_error:
                with pytest.raises(ArchiveyError):
                    ar.read(act)
            elif exp.type is MemberType.FILE:
                assert ar.read(act) == exp.contents, f"contents of {name!r}"
            elif exp.link_contents is not None:
                assert ar.read(act) == exp.link_contents, f"link contents of {name!r}"


def _check_extraction(tmp_path: Path, source, entry: CorpusEntry, key: str) -> None:
    dest = tmp_path / "extracted"
    with open_archive(source, password=list(entry.passwords) or None) as ar:
        results = ar.extract_all(
            dest,
            on_error=OnError.CONTINUE,
        ).results

    by_member_name: dict[str, list] = {}
    for r in results:
        by_member_name.setdefault(r.member.name, []).append(r)

    # Every adversarial member must be BLOCKED; nothing may have been written for it.
    for m in entry.members:
        if m.unsafe:
            statuses = {r.status for r in by_member_name.get(m.name, [])}
            assert statuses == {ExtractionStatus.BLOCKED}, f"{m.name!r} not blocked"

    # Safe FILE members: last occurrence per name wins on disk, contents must match.
    last_safe_file: dict[str, Member] = {}
    for m in entry.members:
        if m.type is MemberType.FILE and not m.unsafe:
            last_safe_file[m.name] = m
    for name, m in last_safe_file.items():
        on_disk = dest / name
        assert on_disk.is_file(), f"{name!r} missing from extraction"
        assert on_disk.read_bytes() == m.contents, f"on-disk contents of {name!r}"

    # Safe hardlinks with known terminal contents share that content on disk.
    if os.name != "nt":
        for m in entry.members:
            if (
                m.type is MemberType.HARDLINK
                and not m.unsafe
                and m.link_contents is not None
            ):
                on_disk = dest / m.name
                assert on_disk.is_file(), f"hardlink {m.name!r} missing"
                assert on_disk.read_bytes() == m.link_contents


@pytest.mark.parametrize(("entry", "key"), _PARAMS)
def test_corpus_conformance(entry: CorpusEntry, key: str, tmp_path: Path) -> None:
    _skip_unless_runnable(entry, key)
    source = corpus_archive_path(entry, key, tmp_path)

    if key in _SINGLE_FILE_KEYS:
        _check_single_file(entry, key, source)
        return

    with open_archive(source, password=list(entry.passwords) or None) as ar:
        _check_listing(ar, entry, key)
        _check_reads(ar, entry)

    _check_extraction(tmp_path, source, entry, key)

    # Encrypted entries must be unreadable without their password (open still works:
    # ZIP encryption is per-member), raising EncryptionError — never wrong data.
    if entry.passwords:
        with open_archive(source) as ar:
            encrypted = next(m for m in entry.members if m.password)
            with pytest.raises(EncryptionError):
                ar.read(encrypted.name)


def _check_single_file(entry: CorpusEntry, key: str, source: Path) -> None:
    (payload,) = entry.members
    with open_archive(source) as ar:
        (member,) = ar.members()
        # The member name is inferred from the archive filename (extension stripped).
        assert member.name == entry.id
        assert ar.read(member) == payload.contents
        if key == "gz-meta":
            # gzip FNAME/MTIME surface as metadata, not as the member's name.
            assert member.extra.get("gzip.original_filename") == payload.name
            assert member.raw_name == payload.name.encode()
            assert member.modified is not None
            assert int(member.modified.timestamp()) == payload.mtime
        # Stored-digest parity: single-member gzip always (path source); lzip only with
        # declared SEEKABLE (same gate as size); zlib Adler-32 on seekable/path; others omit.
        if key in ("gz", "gz-meta"):
            assert HashAlgorithm.CRC32 in member.hashes
        elif key == "lz":
            assert HashAlgorithm.CRC32 not in member.hashes
        elif key == "zz":
            assert HashAlgorithm.ADLER32 in member.hashes
            assert HashAlgorithm.CRC32 not in member.hashes
        else:
            assert HashAlgorithm.CRC32 not in member.hashes
            assert HashAlgorithm.BLAKE2SP not in member.hashes
            assert HashAlgorithm.ADLER32 not in member.hashes
    if key == "lz":
        with open_archive(source, member_streams=MemberStreams.SEEKABLE) as ar:
            assert HashAlgorithm.CRC32 in ar.members()[0].hashes
