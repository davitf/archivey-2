"""Archive-data-model contract tests (phase-5 task 6.3 audit).

Pure data-type behaviours required by ``archive-data-model`` that the per-format tests
don't exercise directly: ``ArchiveFormat`` identity (compositional round-trip, on-demand
construction of uncommon container×codec pairs, ``file_extension``) and the
``ArchiveMember`` value-object contract (unhashable, copy-on-edit via ``replace``,
``None`` defaults, equality excluding hashes/extra, and the type helpers).
"""

from __future__ import annotations

import pytest

from archivey.types import (
    EXTRA_IS_JUNCTION,
    ArchiveFormat,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    MemberType,
    StreamFormat,
)

# ---------------------------------------------------------------------------
# ArchiveFormat identity (compositional (container, stream) model)
# ---------------------------------------------------------------------------


def test_format_identity_round_trips_through_pair() -> None:
    assert ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP) == ArchiveFormat.TAR_GZ


def test_standalone_lzip_has_named_format() -> None:
    assert ArchiveFormat.LZIP.container == ContainerFormat.RAW_STREAM
    assert ArchiveFormat.LZIP.stream == StreamFormat.LZIP


def test_uncommon_container_codec_built_on_demand() -> None:
    # tar.lz has no predefined TAR_LZIP constant, but is constructed on demand and compares
    # equal to any other instance with the same (container, stream) pair.
    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)
    assert fmt == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)
    assert fmt.file_extension() == "tar.lz"


def test_file_extension_examples() -> None:
    assert ArchiveFormat.ZIP.file_extension() == "zip"
    assert ArchiveFormat.TAR_GZ.file_extension() == "tar.gz"
    assert ArchiveFormat.GZ.file_extension() == "gz"
    # Formats with no on-disk file representation return "".
    assert ArchiveFormat.DIRECTORY.file_extension() == ""
    assert ArchiveFormat.UNKNOWN.file_extension() == ""


# ---------------------------------------------------------------------------
# ArchiveMember value-object contract
# ---------------------------------------------------------------------------


def test_member_is_unhashable() -> None:
    m = ArchiveMember(type=MemberType.FILE, name="a.txt")
    with pytest.raises(TypeError):
        hash(m)
    with pytest.raises(TypeError):
        _ = {m}  # set membership needs hashing → unhashable


def test_replace_returns_copy_without_mutating_original() -> None:
    m = ArchiveMember(type=MemberType.FILE, name="a.txt", mode=0o644)
    copy = m.replace(name="b.txt")
    assert copy is not m
    assert copy.name == "b.txt"
    assert copy.mode == 0o644  # untouched fields carried over
    assert m.name == "a.txt"  # original never mutated


def test_unavailable_fields_default_to_none() -> None:
    # The library must not substitute silent defaults for fields a format cannot provide.
    m = ArchiveMember(type=MemberType.FILE, name="a.txt")
    assert m.size is None
    assert m.compressed_size is None
    assert m.mode is None
    assert m.modified is None
    assert m.link_target is None
    assert m.link_target_member is None
    assert m.compression == ()


def test_equality_excludes_hashes_and_extra() -> None:
    # hashes vary by format and extra is format-specific overflow; neither affects logical
    # identity, so both are excluded from __eq__.
    a = ArchiveMember(
        type=MemberType.FILE, name="a.txt", hashes={"crc32": 1}, extra={"x": 1}
    )
    b = ArchiveMember(
        type=MemberType.FILE, name="a.txt", hashes={"crc32": 2}, extra={"y": 2}
    )
    assert a == b


def test_single_codec_member_compression_shape() -> None:
    m = ArchiveMember(
        type=MemberType.FILE,
        name="a",
        compression=(CompressionMethod(CompressionAlgorithm.DEFLATE),),
    )
    assert m.compression == (CompressionMethod(algo=CompressionAlgorithm.DEFLATE),)


def test_type_helpers() -> None:
    assert ArchiveMember(type=MemberType.FILE, name="f").is_file
    assert ArchiveMember(type=MemberType.DIRECTORY, name="d/").is_dir
    assert ArchiveMember(type=MemberType.SYMLINK, name="s").is_link
    assert ArchiveMember(type=MemberType.HARDLINK, name="h").is_link
    assert ArchiveMember(type=MemberType.OTHER, name="o").is_other


def test_junction_helper() -> None:
    junction = ArchiveMember(
        type=MemberType.SYMLINK, name="j", extra={EXTRA_IS_JUNCTION: True}
    )
    assert junction.is_junction
    assert not ArchiveMember(type=MemberType.SYMLINK, name="s").is_junction
