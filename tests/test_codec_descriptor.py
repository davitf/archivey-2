"""The StreamCodec object is the single source of truth for a single-stream codec.

Registering one synthetic StreamCodec subclass must make a new standalone codec detectable,
readable as a one-member archive, and availability-reported — without touching the detector,
the single-file reader, or the registry's availability logic (see ``compressed-streams``).
"""

from __future__ import annotations

import enum
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from archivey import open_archive
from archivey.formats.single_file_reader import SingleFileBackend
from archivey.internal import registry as registry_module
from archivey.internal.detection import DetectionConfidence, detect_format
from archivey.internal.registry import FormatSupport, format_availability, get_registry
from archivey.internal.streams import codecs
from archivey.internal.streams.streamtools import ensure_binaryio
from archivey.internal.types import (
    ArchiveFormat,
    ContainerFormat,
    MagicSignature,
    MissingComponent,
)

# The synthetic codec stores its payload verbatim behind a unique 6-byte magic, so detection
# matches on the magic and reading is a passthrough whose output equals the stored payload.
_MAGIC = b"SYNTH\x00"


class _SyntheticCodecId(enum.Enum):
    SYNTHETIC = "synthetic"


class _SyntheticStream(enum.Enum):
    SYNTHETIC = "syn"


# A bare ArchiveFormat (not one of the named singletons) for the synthetic standalone format.
_FORMAT = ArchiveFormat(ContainerFormat.RAW_STREAM, _SyntheticStream.SYNTHETIC)


@contextmanager
def _install_synthetic(
    monkeypatch: pytest.MonkeyPatch,
    *,
    requirement: MissingComponent | None,
    available: bool = True,
) -> Iterator[ArchiveFormat]:
    """Register one synthetic StreamCodec subclass, then restore on exit."""

    class SyntheticCodec(codecs.StreamCodec):
        codec = _SyntheticCodecId.SYNTHETIC  # type: ignore[assignment]
        stream_format = _SyntheticStream.SYNTHETIC  # type: ignore[assignment]
        single_file_format = _FORMAT
        magic = (MagicSignature(0, _MAGIC, _FORMAT),)
        extensions = (".syn",)

        def open(self, source, params, config):  # type: ignore[no-untyped-def]
            if isinstance(source, (str, os.PathLike)):
                return open(os.fspath(source), "rb")
            return ensure_binaryio(source)

        def extract_metadata(self, ctx, member):  # type: ignore[no-untyped-def]
            # Prove the codec's metadata hook runs through the reader's context.
            member.extra["synthetic.header_len"] = len(ctx.peek_header(len(_MAGIC)))

        def _backend_present(self) -> bool:
            return available

    SyntheticCodec.requirement = requirement
    obj = SyntheticCodec()

    reg = get_registry()
    # The codec registry tuples are imported by reference into the registry module, so patch
    # both namespaces; the by-codec/by-format dicts are read live, so setitem suffices. The
    # registry's _readers/_reader_classes are copied so register_reader's mutation is undone.
    new_stream = codecs.STREAM_CODECS + (obj,)
    new_single = codecs.SINGLE_FILE_CODECS + (obj,)
    monkeypatch.setattr(codecs, "STREAM_CODECS", new_stream)
    monkeypatch.setattr(codecs, "SINGLE_FILE_CODECS", new_single)
    monkeypatch.setattr(registry_module, "STREAM_CODECS", new_stream)
    monkeypatch.setattr(registry_module, "SINGLE_FILE_CODECS", new_single)
    monkeypatch.setitem(codecs._BY_CODEC, obj.codec, obj)
    monkeypatch.setitem(codecs._BY_STREAM_FORMAT, obj.stream_format, obj)
    monkeypatch.setattr(reg, "_readers", dict(reg._readers))
    monkeypatch.setattr(reg, "_reader_classes", list(reg._reader_classes))
    monkeypatch.setattr(
        SingleFileBackend, "FORMATS", SingleFileBackend.FORMATS + (_FORMAT,)
    )
    reg.register_reader(SingleFileBackend)  # map the new format -> backend
    yield _FORMAT


def test_synthetic_descriptor_is_detectable_and_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"hello from a one-descriptor codec"
    data = _MAGIC + payload
    with _install_synthetic(monkeypatch, requirement=None) as fmt:
        # 1. Detected by its codec's magic, no detector edits.
        info = detect_format(io.BytesIO(data))
        assert info.format == fmt
        assert info.confidence == DetectionConfidence.CERTAIN
        assert info.detected_by == "magic"

        # 2. Read as a one-member archive through the single-file reader, no reader edits.
        with open_archive(io.BytesIO(data)) as ar:
            assert ar.format == fmt
            members = ar.members()
            assert len(members) == 1
            assert members[0].is_file
            # The codec's metadata hook ran via the reader's context.
            assert members[0].extra["synthetic.header_len"] == len(_MAGIC)
            assert ar.read(members[0]) == data  # passthrough codec stores verbatim

        # 3. Availability reported FULL (no requirement -> always available).
        assert format_availability(fmt).support is FormatSupport.FULL


def test_synthetic_descriptor_availability_comes_from_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement = MissingComponent("synthlib", "pip install archivey[synth]", ("synthetic",))
    with _install_synthetic(monkeypatch, requirement=requirement, available=False) as fmt:
        # The (simulated) missing backend makes the single-codec format unreadable -> NONE,
        # with the install hint taken from the codec's requirement (no registry table).
        availability = format_availability(fmt)
        assert availability.support is FormatSupport.NONE
        assert availability.missing == (requirement,)
