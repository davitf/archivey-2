"""Guardrails for the simplicity & consistency review (`review/simplicity-consistency/`).

The file started as two kinds of test: **guardrails** (plain assertions pinning a rule
the review classified as *format law* or *settled*) and **red halves**
(``@pytest.mark.xfail(strict=True)`` assertions of the behaviour the review argued was
correct, failing on purpose until a fix landed).

**All sixteen red halves have now landed** (W1–W8), so what remains is one regression
net, and it is deliberately kept in one file: each test names the finding it came from,
so a future change that reintroduces a divergence fails against the argument that
removed it rather than against a bare assertion. Where a fix went a different way than
the review recommended — Q2 and Q7 chose a diagnostic over the refusal the red halves
asserted — the test says so, because the *rejected* option is the one someone will
propose again.

Several tests also pin the half of a fix that is easy to lose: a pipe still reports no
cheap metadata (F1), a genuinely missing path still says "not found" (F11), a corrupt
member offset is still corruption (F4), a *small* rewind still stays quiet (F19), and a
directional bidi mark still extracts (O7).

Every test names the merged finding id from
`review/simplicity-consistency/SUMMARY.md` so the two stay linked. The review was
delivered twice independently (PR #230 and PR #231) and merged; findings carried over
from the second pass are marked in the SUMMARY's provenance column and their guardrails
were moved here from `review/simplicity-consistency/tests/`, which `testpaths =
["tests"]` never collected — a guardrail CI does not run guards nothing.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyConfig,
    ArchiveyUsageError,
    FormatSupport,
    StreamNotSeekableError,
    format_availability,
    open_archive,
    open_stream,
)
from archivey.types import MemberType
from tests.sample_archives import (
    CORPUS,
    CorpusEntry,
    corpus_archive_path,
    skip_unless_runnable,
)

_FILE = MemberType.FILE

_BY_ID: dict[str, CorpusEntry] = {e.id: e for e in CORPUS}


def _entry(entry_id: str) -> CorpusEntry:
    return _BY_ID[entry_id]


def _archive(entry_id: str, key: str, tmp_path: Path) -> Path:
    """Build one corpus archive, skipping cleanly when this env cannot make it.

    Uses the shared ``skip_unless_runnable`` gate rather than a local copy: a missing
    reader *or a missing builder* is an **unmeasured** cell, not a pass. An earlier
    hand-rolled version of this checked only reader availability, which is exactly
    wrong for 7z — the reader is native, so it looked fine, while the corpus builder
    needs ``py7zr`` and the ``core-only`` CI leg died on ``ModuleNotFoundError``.
    """
    entry = _entry(entry_id)
    if key not in entry.formats:
        pytest.skip(f"corpus entry {entry_id!r} is not built as {key!r}")
    skip_unless_runnable(entry, key)
    try:
        return corpus_archive_path(entry, key, tmp_path)
    except FileNotFoundError as exc:  # a builder binary the tables do not name
        pytest.skip(f"builder for {key!r} unavailable: {exc}")


class _NonSeekable(io.RawIOBase):
    """Forward-only byte source — the pipe shape."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# F1 — declared member-stream seekability leaks into member *metadata*
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["lz", "xz"])
@pytest.mark.parametrize("as_stream", [False, True], ids=["path", "bytesio"])
def test_member_size_does_not_depend_on_declared_seekability(
    key: str, as_stream: bool, tmp_path: Path
) -> None:
    """F1 (fixed): the same archive reports the same ``size`` either way.

    The size comes from the xz index / lzip trailer — a bounded peek over a source that
    is already seekable. Nothing about it needs the caller to want ``seek()``, and
    ``seekable_members`` is documented as a *member-stream* capability. Both source
    shapes are checked because the divergence held for a `Path` and a `BytesIO` alike.
    """
    path = _archive("single-file", key, tmp_path)

    def _size(**kwargs: object) -> int | None:
        source = io.BytesIO(path.read_bytes()) if as_stream else path
        with open_archive(source, **kwargs) as reader:  # type: ignore[arg-type]
            return reader.members()[0].size

    assert _size() == _size(seekable_members=True)
    assert isinstance(_size(), int)


def test_lzip_surfaces_crc32_without_declaring_seekable_members(
    tmp_path: Path,
) -> None:
    """F1 (fixed): a dedupe caller doing a plain ``open_archive`` gets the CRC-32.

    ``format-single-file-compressors`` promises the lzip CRC-32 from the index on a
    seekable source; the gate used to be the caller's ``seekable_members`` flag, so the
    founding dedupe use case (`VISION.md` "hashes without decompression") missed it on
    the default open.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", "lz", tmp_path)
    with open_archive(path) as reader:
        assert HashAlgorithm.CRC32 in reader.members()[0].hashes


@pytest.mark.parametrize("key", ["lz", "xz"])
def test_pipe_metadata_stays_absent_and_costs_no_decode(
    key: str, tmp_path: Path
) -> None:
    """F1 (guardrail): the gate moved to the *source's* shape, not away entirely.

    A non-seekable source has no cheap index read, so ``size`` stays ``None`` and no
    digest appears — the fix must not turn the harvest into a decompression pass.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", key, tmp_path)
    with open_archive(_NonSeekable(path.read_bytes()), streaming=True) as reader:
        member = next(iter(reader))
        assert member.size is None
        assert HashAlgorithm.CRC32 not in member.hashes


def test_gzip_crc32_is_not_gated_on_declared_seekability(tmp_path: Path) -> None:
    """F1 (guardrail): gzip already does it the right way — keep it that way.

    The gzip trailer CRC-32 is surfaced from a bounded peek regardless of
    ``seekable_members``. This is the behaviour the lzip/xz rows should converge on,
    so it is pinned rather than left to drift toward the gated shape.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", "gz", tmp_path)
    for kwargs in ({}, {"seekable_members": True}):
        with open_archive(path, **kwargs) as reader:  # type: ignore[arg-type]
            assert HashAlgorithm.CRC32 in reader.members()[0].hashes


# ---------------------------------------------------------------------------
# F6 — index-topology table vs the directory backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_directory_report_peek_returns_none(streaming: bool, tmp_path: Path) -> None:
    """F6 (fixed, spec side): the directory backend has no upfront index to peek at.

    `access-mode-and-cost` used to list "Leading (directory, ISO)" as offering a complete
    report in both modes, which the directory backend never did. The **spec** was the
    thing that was wrong: a filesystem walk is not an index, and the backend's own
    `listing_cost=REQUIRES_SCANNING` already said so. The table now has a scan-based row.
    """
    from archivey.cost import ListingCost

    path = _archive("basic", "dir", tmp_path)
    with open_archive(path, streaming=streaming) as reader:
        assert reader.members_report_if_available() is None
        # The receipt the spec now agrees with.
        assert reader.cost.listing_cost is ListingCost.REQUIRES_SCANNING


@pytest.mark.parametrize("key", ["iso", "zip", "7z"])
def test_leading_and_trailing_index_backends_do_offer_a_report_peek(
    key: str, tmp_path: Path
) -> None:
    """F6 (guardrail): the backends the topology table covers correctly still do."""
    path = _archive("basic", key, tmp_path)
    with open_archive(path) as reader:
        report = reader.members_report_if_available()
        assert report is not None
        assert report.error is None
        assert len(report.members) > 0


def test_tar_has_no_report_peek_before_a_pass(tmp_path: Path) -> None:
    """F6 (guardrail): the no-index row of the topology table — format law, pinned."""
    path = _archive("basic", "tar", tmp_path)
    with open_archive(path) as reader:
        assert reader.members_report_if_available() is None


# ---------------------------------------------------------------------------
# F7 — an explicit wrong format= can succeed with an empty listing
# ---------------------------------------------------------------------------


def test_wrong_explicit_format_on_iso_yields_an_empty_listing(tmp_path: Path) -> None:
    """F7 (pin): ``format=TAR`` over an ISO opens and lists zero members.

    An ISO's first 32 KiB system area is zero-filled, which is byte-identical to a TAR
    end-of-archive marker, so the TAR reader sees a valid empty archive. It still opens
    — `format=` is an override — and the disagreement is now reported as data rather
    than nothing (see `test_wrong_explicit_format_does_not_silently_succeed`).

    Only the default is pinned here; the ``strict_archive_eof=True`` half moved to
    `test_wrong_explicit_format_on_iso_is_caught_by_strict_eof`, because that flag now
    does catch it.
    """
    path = _archive("basic", "iso", tmp_path)
    config = ArchiveyConfig(strict_archive_eof=False)
    with open_archive(path, format=ArchiveFormat.TAR, config=config) as reader:
        assert reader.format == ArchiveFormat.TAR
        assert reader.members() == []


def test_wrong_explicit_format_does_not_silently_succeed(tmp_path: Path) -> None:
    """F7 (fixed): the empty reader is still returned, but the disagreement is data.

    `format=` stays an override — wrong extensions are normal (`VISION.md`) and some
    callers use `format=` precisely to overrule them, so refusing here would take away
    the thing the argument is for. What was wrong was the *silence*: a clean empty
    listing under an asserted format that the bytes contradict.
    """
    from archivey.diagnostics import DiagnosticCode

    path = _archive("basic", "iso", tmp_path)
    with open_archive(path, format=ArchiveFormat.TAR) as reader:
        assert reader.members() == []
        counts = reader.diagnostics.counts
        assert counts[DiagnosticCode.EMPTY_ARCHIVE] == 1
        assert counts[DiagnosticCode.EXPLICIT_FORMAT_LISTED_EMPTY] == 1
        (record,) = [
            d
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.EXPLICIT_FORMAT_LISTED_EMPTY
        ]
        assert record.context.detected_format == "ISO"  # type: ignore[union-attr]


def test_right_explicit_format_stays_silent(tmp_path: Path) -> None:
    """F7 (guardrail): the check must cost nothing and say nothing on a real archive."""
    from archivey.diagnostics import DiagnosticCode

    path = _archive("basic", "tar", tmp_path)
    with open_archive(path, format=ArchiveFormat.TAR) as reader:
        assert reader.members()
        assert DiagnosticCode.EMPTY_ARCHIVE not in reader.diagnostics.counts
        assert (
            DiagnosticCode.EXPLICIT_FORMAT_LISTED_EMPTY not in reader.diagnostics.counts
        )


def test_wrong_explicit_format_is_loud_for_most_formats(tmp_path: Path) -> None:
    """F7 (guardrail): the non-zero-prefixed formats do fail loudly — keep them loud."""
    path = _archive("basic", "zip", tmp_path)
    with pytest.raises(Exception):  # noqa: B017
        with open_archive(path, format=ArchiveFormat.TAR) as reader:
            reader.members()


# ---------------------------------------------------------------------------
# F2 — encoding= is honoured by some backends and silently discarded by others
# ---------------------------------------------------------------------------


def _names_with_and_without_encoding(path: Path) -> tuple[list[str], list[str]]:
    with open_archive(path) as reader:
        base = [m.name for m in reader.members()]
    # cp500 (EBCDIC) re-maps even ASCII, so "unchanged" means "not applied at all"
    # rather than "applied but this corpus has no non-ASCII names".
    with open_archive(path, encoding="cp500") as reader:
        alt = [m.name for m in reader.members()]
    return base, alt


@pytest.mark.parametrize("key", ["zip", "tar"])
def test_encoding_argument_is_applied(key: str, tmp_path: Path) -> None:
    """F2 (guardrail): the backends that consume ``encoding=`` still consume it."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base != alt


@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_encoding_argument_is_silently_discarded(key: str, tmp_path: Path) -> None:
    """F2 (pin): these backends accept ``encoding=`` and ignore it, with no signal."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base == alt


@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_unusable_encoding_argument_is_recorded(key: str, tmp_path: Path) -> None:
    """F2 (fixed): the discard is queryable — but the entry point stays permissive.

    The review recommended refusing (the `#225`/P8 rule generalised). The maintainer
    chose the softer option, and O5 then supplied the principle behind it: `encoding=`
    is a **resource offered for use if needed**, not an assertion about this archive,
    and the batch caller who passes one configuration across heterogeneous input should
    not have it fail on the ISO in the middle.
    """
    from archivey.diagnostics import DiagnosticCode

    path = _archive("basic", key, tmp_path)
    with open_archive(path, encoding="cp500") as reader:
        assert reader.diagnostics.counts[DiagnosticCode.ENCODING_ARGUMENT_UNUSED] == 1


@pytest.mark.parametrize("key", ["zip", "tar"])
def test_usable_encoding_argument_is_not_recorded(key: str, tmp_path: Path) -> None:
    """F2 (guardrail): a backend that consumes `encoding=` must stay silent about it."""
    from archivey.diagnostics import DiagnosticCode

    path = _archive("basic", key, tmp_path)
    with open_archive(path, encoding="cp500") as reader:
        reader.members()
        assert DiagnosticCode.ENCODING_ARGUMENT_UNUSED not in reader.diagnostics.counts


# ---------------------------------------------------------------------------
# F8 — pipe support: loud and uniform (good), but not queryable (the finding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["zip", "iso", "7z"])
def test_trailing_index_formats_refuse_a_pipe_loudly(key: str, tmp_path: Path) -> None:
    """F8 (guardrail): the refusal is one typed error with one message shape.

    This half of the seed turned out to be **fine**, and pinning it is what keeps it
    fine: a backend that started failing softly here would be a regression.
    """
    path = _archive("basic", key, tmp_path)
    data = path.read_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(_NonSeekable(data), streaming=True)


@pytest.mark.parametrize("key", ["tar", "tar.gz"])
def test_front_indexed_formats_accept_a_pipe(key: str, tmp_path: Path) -> None:
    """F8 (guardrail): the other side of the same rule."""
    path = _archive("basic", key, tmp_path)
    with open_archive(_NonSeekable(path.read_bytes()), streaming=True) as reader:
        assert sum(1 for _ in reader) > 0


# ---------------------------------------------------------------------------
# F11 — the two entry points disagree about what a directory is
# ---------------------------------------------------------------------------


def test_open_stream_missing_path_is_still_not_found(tmp_path: Path) -> None:
    """F11 (guardrail): the genuinely-absent case keeps its ``FileNotFoundError``.

    The fix below splits the old ``is_file()`` predicate; this pins the half that was
    always right, so the split cannot swallow it.
    """
    with pytest.raises(FileNotFoundError, match="Compressed stream not found"):
        open_stream(tmp_path / "nothing-here.gz")


def test_open_stream_directory_error_names_the_real_problem(tmp_path: Path) -> None:
    """F11 (fixed): the message must not claim the path is absent.

    Asserted as the *absence* of "not found" rather than the presence of "directory":
    the message interpolates the path, and a pytest tmp dir carries the test's own name,
    so a positive substring check would pass for the wrong reason.
    """
    d = tmp_path / "tree"
    d.mkdir()
    with pytest.raises(ArchiveyUsageError) as excinfo:
        open_stream(d)
    message = str(excinfo.value).replace(str(d), "<path>").lower()
    assert "not found" not in message


def test_open_archive_opens_the_directory_open_stream_rejects(tmp_path: Path) -> None:
    """F11 (guardrail): the asymmetry itself, pinned so a fix has to address both sides."""
    d = tmp_path / "tree"
    d.mkdir()
    (d / "a.txt").write_bytes(b"hello")
    with open_archive(d) as reader:
        assert [m.name for m in reader.members()] == ["a.txt"]


# ---------------------------------------------------------------------------
# The uniform surface that *is* uniform — pinned so it stays that way
# ---------------------------------------------------------------------------

_UNIFORM_KEYS = ["zip", "tar", "tar.gz", "iso", "7z", "dir", "gz"]


@pytest.mark.parametrize("key", _UNIFORM_KEYS)
def test_reader_surface_is_uniform_across_formats(key: str, tmp_path: Path) -> None:
    """The review's main negative result: these rows agree on every measured backend.

    The probe found no format that diverges on any of them. Pinning them here is the
    cheap half of the review — a future backend that gets one wrong fails a test rather
    than becoming a Gotchas bullet.
    """
    entry_id = "single-file" if key == "gz" else "basic"
    path = _archive(entry_id, key, tmp_path)

    with open_archive(path) as reader:
        with pytest.raises(TypeError):
            len(reader)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            "some-name" in reader  # type: ignore[operator]
        assert reader.get("no-such-member") is None
        with pytest.raises(KeyError):
            reader.read("no-such-member")

        member = next(m for m in reader.members() if m.type.name == "FILE")
        stream = reader.open(member)
        try:
            with pytest.raises(ArchiveyUsageError):
                reader.open(member)
            with pytest.raises(io.UnsupportedOperation):
                stream.seek(0)
        finally:
            stream.close()

    with pytest.raises(ArchiveyUsageError):
        reader.format  # noqa: B018 - property access after close must raise


@pytest.mark.parametrize("key", _UNIFORM_KEYS)
def test_streaming_mode_is_uniform_across_formats(key: str, tmp_path: Path) -> None:
    """Streaming enforcement agrees on every backend that can stream from a path."""
    entry_id = "single-file" if key == "gz" else "basic"
    path = _archive(entry_id, key, tmp_path)

    from archivey import UnsupportedOperationError

    with open_archive(path, streaming=True) as reader:
        for op in (
            lambda: reader.members(),
            lambda: reader.get("x"),
            lambda: reader.open("x"),
            lambda: reader.read("x"),
        ):
            with pytest.raises(UnsupportedOperationError):
                op()

        assert sum(1 for _ in reader) >= 0
        with pytest.raises(UnsupportedOperationError):
            for _ in reader:
                pass


def test_rar_column_is_unmeasured_without_the_rar_writer() -> None:
    """The coupling: RAR readability does not imply RAR *measurability*.

    ``unrar`` (the decompressor) is enough to *read* RAR, but the corpus builds its RAR
    fixtures with the RARLAB ``rar`` writer, so a green suite on an unrar-only box says
    nothing about the RAR column of the conformance sweep.

    The writer is now installed on Linux (`scripts/setup-dev-env.sh`, and the Linux
    `[all]` CI leg), so this test skips there. It still runs on macOS and Windows, where
    the writer is deliberately absent — the RAR column is claimed on Linux only, because
    nothing in the diagnosis was plausibly platform-specific but that is an expectation
    rather than a measurement.

    The old justification for withholding the writer everywhere — "the RAR fixtures'
    digest expectations are Linux-fixture-oriented" — did not survive being measured:
    the corpus makes no platform-dependent assertion about a RAR member, and the sweep
    was failing on two wrong assertions in the test itself. F16 / Q11 / O6; full
    evidence in `dev-docs/investigations/rar-corpus-sweep-diagnosis.md`.
    """
    rar_is_readable = format_availability(ArchiveFormat.RAR).support is not (
        FormatSupport.NONE
    )
    if shutil.which("rar") is not None:
        pytest.skip("rar writer present — the RAR corpus column is measurable here")
    assert rar_is_readable, "unrar present: RAR reads fine, yet no RAR fixture is built"


# ---------------------------------------------------------------------------
# F3 — raw ValueError crosses open_archive for volume-sequence misuse
# (merged from the second review pass; verified independently here)
# ---------------------------------------------------------------------------


def test_empty_source_sequence_is_a_usage_error() -> None:
    """F3 (fixed): an empty sequence is caller misuse, so `ArchiveyUsageError`.

    `resolve_source` runs before any backend translator exists, so this had to be typed
    at the raise site rather than by a translator.
    """
    with pytest.raises(ArchiveyUsageError):
        open_archive([])


def test_non_seekable_volume_sequence_is_a_stream_error() -> None:
    """F3 (fixed): match the single-source spelling of the same refusal.

    The *single*-source version of this refusal is already `StreamNotSeekableError`
    (see the pipe guardrails above); a volume sequence is the same refusal.
    """
    with pytest.raises(StreamNotSeekableError):
        open_archive([_NonSeekable(b"x" * 100), _NonSeekable(b"y" * 100)])


# ---------------------------------------------------------------------------
# F4 — ZIP maps every ValueError to CorruptionError, including "already closed"
# ---------------------------------------------------------------------------


def _zip_bytes() -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    return buf.getvalue()


def test_zip_underlying_close_is_a_usage_error() -> None:
    """F4 (fixed): the archive bytes are fine — the handle is not.

    Distinct from the settled `#225` behaviour: a normal `reader.close()` followed by
    `open()` already raises `ArchiveyUsageError` (pinned by the uniform-surface test
    above). This path — the underlying handle closed while the reader still believes it
    is open — used to land in the ZIP translator's blanket `ValueError` arm and report
    `CorruptionError`, sending a caller hunting a bad file.
    """
    with open_archive(io.BytesIO(_zip_bytes())) as reader:
        member = next(m for m in reader.members() if m.type is _FILE)
        reader._archive.close()  # type: ignore[attr-defined]  # deliberate: simulate the fault
        with pytest.raises(ArchiveyUsageError):
            reader.open(member)


def test_zip_corrupt_member_offset_is_still_corruption(tmp_path: Path) -> None:
    """F4 (guardrail): the blanket `ValueError` arm still means damage for real damage.

    The closed-handle carve-out must not take the corrupt-local-header-offset case with
    it — that one really is a bad file, and `CorruptionError` is the right answer.
    """
    from archivey import CorruptionError

    data = bytearray(_zip_bytes())
    # The central directory's local-header offset field, clobbered so zipfile seeks to a
    # nonsense position when the member is opened.
    cd_start = data.rindex(b"PK\x01\x02")
    data[cd_start + 42 : cd_start + 46] = (0xFFFFFFF0).to_bytes(4, "little")

    with open_archive(io.BytesIO(bytes(data))) as reader:
        member = next(m for m in reader.members() if m.type is _FILE)
        with pytest.raises(CorruptionError):
            reader.open(member)


# ---------------------------------------------------------------------------
# F5 — single-file compressed_size is still Path-gated
# ---------------------------------------------------------------------------

_SINGLE_FILE_KEYS = ["gz", "bz2", "xz", "zst", "lz4", "lz", "zz", "br"]


@pytest.mark.parametrize("key", _SINGLE_FILE_KEYS)
def test_single_file_compressed_size_is_not_path_gated(
    key: str, tmp_path: Path
) -> None:
    """F5 (fixed): source shape must not decide whether the field exists.

    This was the residual the `#225` Path/seekable sweep did not reach, and it held for
    **every** single-file codec, not just gzip: `_build_member` used `os.path.getsize`
    behind an `isinstance(..., Path)` check with no seekable-stream fallback, while the
    trailer/CRC probes beside it already handled both. Parametrized over all nine codecs
    because the bug was in the shared shell, not in any one codec.
    """
    path = _archive("single-file", key, tmp_path)
    with open_archive(path) as reader:
        from_path = reader.members()[0].compressed_size
    with open_archive(io.BytesIO(path.read_bytes())) as reader:
        from_stream = reader.members()[0].compressed_size

    assert from_path == from_stream == path.stat().st_size


def test_single_file_compressed_size_is_absent_for_a_pipe(tmp_path: Path) -> None:
    """F5 (guardrail): a non-seekable source still reports `None` — no forced scan.

    The fix widens the gate from "is a Path" to "is seekable", not to "read it and
    count", which would cost a whole pass at open on a pipe.
    """
    path = _archive("single-file", "gz", tmp_path)
    with open_archive(_NonSeekable(path.read_bytes()), streaming=True) as reader:
        assert next(iter(reader)).compressed_size is None


def test_container_formats_report_compressed_size_from_either_shape(
    tmp_path: Path,
) -> None:
    """F5 (guardrail): the container backends already do it right — keep them there."""
    path = _archive("basic", "zip", tmp_path)
    with open_archive(path) as reader:
        from_path = next(m for m in reader.members() if m.type is _FILE).compressed_size
    with open_archive(io.BytesIO(path.read_bytes())) as reader:
        from_stream = next(
            m for m in reader.members() if m.type is _FILE
        ).compressed_size
    assert from_path == from_stream and from_path is not None


# ---------------------------------------------------------------------------
# F9 — header encryption needs the password at open (format law), and the
# user guide's laziness bullet does not say so
# ---------------------------------------------------------------------------


def test_header_encrypted_7z_needs_the_password_at_open(tmp_path: Path) -> None:
    """F9 (guardrail): format law — the listing itself is ciphertext.

    Pinned because it bounds the `#225` laziness fix: *data* encryption stays lazy,
    *header* encryption cannot. `docs/reading-members.md` currently states the lazy
    half without the bound (finding F11).
    """
    from archivey import EncryptionError

    entry = _entry("encrypted-header")
    path = _archive("encrypted-header", "7z", tmp_path)

    with pytest.raises(EncryptionError):
        open_archive(path)
    with open_archive(path, password=entry.passwords[0]) as reader:
        assert len(reader.members()) > 0


def test_data_encrypted_members_still_list_without_a_password(tmp_path: Path) -> None:
    """F9 (guardrail): the other half — data encryption stays lazy, per `#225`."""
    entry = _entry("encrypted")
    key = next((k for k in ("zip", "7z") if k in entry.formats), None)
    if key is None:
        pytest.skip("no data-encrypted corpus entry in a measurable format")
    path = _archive("encrypted", key, tmp_path)

    with open_archive(path) as reader:  # no password at all
        assert len(reader.members()) > 0


# ---------------------------------------------------------------------------
# F10 — the bidi/RTL warning is ambient only
# ---------------------------------------------------------------------------


def test_bidi_name_warning_is_queryable_data(tmp_path: Path) -> None:
    """F10 (fixed): the RTL/bidi advisory is a diagnostic, not a bare `logger.warning`.

    `VISION.md`: "anything the library can only *warn* about should ideally also be
    queryable as data — a logging warning most applications never see is a surprise
    deferred, not avoided." This was the library's single ambient-only advisory, while
    its neighbour in the same helper (name normalization) already had a code.

    The context records *which* controls were found, because the override/isolate set
    (rejected during safe extraction) and the directional marks (legitimate in Arabic
    and Hebrew filenames) are not the same category.
    """
    import tarfile

    from archivey.diagnostics import DiagnosticCode

    name = "invoice‮cod.exe"
    path = tmp_path / "bidi.tar"
    with tarfile.open(path, "w") as tar:
        tar.addfile(tarfile.TarInfo(name), io.BytesIO(b""))

    with open_archive(path) as reader:
        assert [m.name for m in reader.members()] == [name]  # presented as stored
        summary = reader.diagnostics
        assert summary.counts[DiagnosticCode.MEMBER_NAME_BIDI_CONTROL] == 1
        (record,) = [
            d
            for d in summary.retained
            if d.code is DiagnosticCode.MEMBER_NAME_BIDI_CONTROL
        ]
        assert record.context.controls == "U+202E"  # type: ignore[union-attr]


def test_bidi_diagnostic_is_emitted_once_per_presentation(tmp_path: Path) -> None:
    """F10 (guardrail): one member, one occurrence — listing twice must not double it.

    The emission lives in `_register_member`, which runs once per member identity, so a
    backend that also normalizes the name cannot add a second.
    """
    import tarfile

    from archivey.diagnostics import DiagnosticCode

    path = tmp_path / "bidi.tar"
    with tarfile.open(path, "w") as tar:
        tar.addfile(tarfile.TarInfo("a‮b.txt"), io.BytesIO(b""))

    with open_archive(path) as reader:
        reader.members()
        reader.members()
        assert reader.diagnostics.counts[DiagnosticCode.MEMBER_NAME_BIDI_CONTROL] == 1


# ---------------------------------------------------------------------------
# F19 — the rewind diagnostic is silent for a degenerate seek index
# ---------------------------------------------------------------------------


def _single_block_xz(tmp_path: Path) -> Path:
    """A one-block .xz over incompressible data — what `lzma.compress` produces.

    `xz` without threading writes a single block too, so this is the common shape,
    not a contrived one.

    2 MB rather than the 1 MB the review used: the fix's threshold is
    `REWIND_REDECODE_WARN_BYTES` = 1 **MiB** (1 048 576), and 1 000 000 sits just under
    it, so the original payload would have measured the threshold rather than the
    blind spot.
    """
    import lzma
    import random

    path = tmp_path / "one_block.xz"
    path.write_bytes(lzma.compress(random.Random(7).randbytes(2_000_000)))
    return path


def test_full_rewind_emits_regardless_of_codec(tmp_path: Path) -> None:
    """F19 (fixed): the same work produces the same signal.

    A backward seek that throws away the whole decoded stream is the event the
    diagnostic exists for. Whether the codec *could* have carried a useful index is
    irrelevant when this particular file does not: a single-block xz has one seek point
    (the origin), so the rewind costs exactly what an index-less codec's does.

    The predicate used to be codec identity, decided once at open, and XZ returned
    ``None`` unconditionally because the *format* can carry a block index.
    """
    from archivey.diagnostics import DiagnosticCode

    path = _single_block_xz(tmp_path)
    with open_stream(path, seekable=True) as stream:
        stream.read()
        stream.seek(10)
        stream.read(4)
        assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES in dict(
            stream.diagnostics.counts
        )


def test_cheap_rewind_stays_silent(tmp_path: Path) -> None:
    """F19 (guardrail): the predicate is cost, so a *small* rewind must go quiet.

    The old codec-identity rule warned on any backward seek in an index-less stream,
    including a 1 KB one, which is noise. This is the other half of the same fix and the
    half a cost-based rule could regress by simply always warning.
    """
    import lzma

    from archivey.diagnostics import DiagnosticCode

    path = tmp_path / "small.lzma"
    path.write_bytes(lzma.compress(b"x" * 1000, format=lzma.FORMAT_ALONE))
    with open_stream(path, seekable=True) as stream:
        stream.read()
        stream.seek(0)
        stream.read(4)
        assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES not in dict(
            stream.diagnostics.counts
        )


def test_rewind_policy_escalates_on_every_qualifying_seek(tmp_path: Path) -> None:
    """F19 / O1: recorded once, escalated always.

    The tripwire is the diagnostic's real job — a `RAISE` policy so a batch job aborts
    instead of silently going quadratic — and a guard that disarms after firing once is
    not a guard. Deduplication is a presentation concern for the report; escalation is
    control flow for a caller who explicitly asked to be stopped.
    """
    from archivey import ArchiveyConfig, DiagnosticRaisedError
    from archivey.diagnostics import (
        DiagnosticCode,
        DiagnosticDisposition,
        DiagnosticPolicy,
    )

    config = ArchiveyConfig(
        diagnostic_policy=DiagnosticPolicy(
            overrides={
                DiagnosticCode.STREAM_REWIND_REDECOMPRESSES: DiagnosticDisposition.RAISE
            }
        )
    )
    path = _single_block_xz(tmp_path)
    with open_stream(path, seekable=True, config=config) as stream:
        stream.read()
        for _ in range(2):
            with pytest.raises(DiagnosticRaisedError):
                stream.seek(10)
            stream.read()
        # ...and the report still holds exactly one record, not one per seek.
        assert (
            stream.diagnostics.counts[DiagnosticCode.STREAM_REWIND_REDECOMPRESSES] == 1
        )


# ---------------------------------------------------------------------------
# F20 — a zero-filled file with a .tar extension opens as an empty archive
# ---------------------------------------------------------------------------


def test_content_detection_refuses_a_zero_filled_file(tmp_path: Path) -> None:
    """F20 (guardrail): magic-byte detection correctly declines to call zeros a TAR.

    This is the layer that behaves well, and it is pinned so a future detection change
    cannot quietly start accepting the shape the other two layers already accept.
    """
    from archivey import FormatDetectionError, detect_format

    path = tmp_path / "zeros.bin"
    path.write_bytes(b"\x00" * 32768)
    with pytest.raises(FormatDetectionError):
        detect_format(path)


@pytest.mark.parametrize("strict_eof", [False, True])
def test_zero_filled_dot_tar_opens_empty_via_extension(
    strict_eof: bool, tmp_path: Path
) -> None:
    """F20 (pin): the *extension* path accepts what content detection refuses.

    32 KiB of zeros named ``z.tar`` opens as TAR with zero members, no error and no
    diagnostic — and ``strict_archive_eof=True`` does not change that, because the two
    null trailer blocks really are present. The knob asserts "the trailer is complete",
    not "the archive ends here".

    This is the realistic form of the wrong-format problem: a zero-truncated file with a
    plausible extension is exactly the shape `VISION.md`'s founding corpus is full of.
    The fix for an explicit ``format=`` does not reach this path, because there is no
    explicit format to disagree with — so this layer has its own code, keyed on the
    format having been chosen by the extension after every content signal declined.

    It still opens: a legitimately empty tar is byte-identical to this file, so no
    predicate over the bytes can refuse one without refusing the other (O8a).
    """
    from archivey.diagnostics import DiagnosticCode

    path = tmp_path / "z.tar"
    path.write_bytes(b"\x00" * 32768)
    config = ArchiveyConfig(strict_archive_eof=strict_eof)
    with open_archive(path, config=config) as reader:
        assert reader.format == ArchiveFormat.TAR
        assert reader.members() == []
        assert dict(reader.diagnostics.counts) == {
            DiagnosticCode.EMPTY_ARCHIVE: 1,
            DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED: 1,
        }


def _one_member_tar() -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    return buf.getvalue()


@pytest.mark.parametrize(
    ("tail", "tail_id"),
    [
        (b"JUNK" * 1024, "junk"),
        (b"\x00" * 4096 + b"X" + b"\x00" * 512, "buried-byte"),
        (_one_member_tar(), "concatenated"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_strict_archive_eof_rejects_trailing_data(
    tail: bytes, tail_id: str, tmp_path: Path
) -> None:
    """F20 (fixed): the knob now asserts what it documents.

    It used to check exactly one thing — that the second null trailer block was present
    — and never looked past it, so 4 KiB of arbitrary appended bytes passed silently
    under the flag you set for a "provably complete listing". Now every byte from the
    trailer to EOF must be zero.

    The concatenated case is deliberate: two tars really are two archives and the reader
    listed only the first.
    """
    from archivey import CorruptionError

    path = tmp_path / f"{tail_id}.tar"
    path.write_bytes(_one_member_tar() + tail)

    # Unchanged without the flag — including the cost, since the scan does not run.
    with open_archive(
        path,
        format=ArchiveFormat.TAR,
        config=ArchiveyConfig(strict_archive_eof=False),
    ) as reader:
        assert [m.name for m in reader.members()] == ["a.txt"]
        assert dict(reader.diagnostics.counts) == {}

    with pytest.raises(CorruptionError):
        with open_archive(
            path,
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(strict_archive_eof=True),
        ) as reader:
            reader.members()


@pytest.mark.parametrize("strict_eof", [False, True])
def test_zero_padding_after_the_trailer_still_passes(
    strict_eof: bool, tmp_path: Path
) -> None:
    """F20 (guardrail): the case the "nothing but zeros" rule exists to preserve.

    `tar` pads to 10 KiB records by default and other writers pad further, so "the file
    ends at the trailer" would reject what `tar(1)` itself produces.
    """
    path = tmp_path / "padded.tar"
    path.write_bytes(_one_member_tar() + b"\x00" * 4096)
    config = ArchiveyConfig(strict_archive_eof=strict_eof)
    with open_archive(path, format=ArchiveFormat.TAR, config=config) as reader:
        assert [m.name for m in reader.members()] == ["a.txt"]
        assert dict(reader.diagnostics.counts) == {}


def test_wrong_explicit_format_on_iso_is_caught_by_strict_eof(tmp_path: Path) -> None:
    """F20: an ISO read as TAR now fails under strict — the review predicted otherwise.

    O8b listed "the ISO case still passes" as a deliberate consequence, on the grounds
    that its 32 KiB system area is zeros and therefore reads as a valid empty TAR with
    padding. Measured, that describes only the first 32768 bytes: the zeros stop exactly
    where the volume descriptors begin (``\\x01CD001``) and ~48 KiB of real data follows,
    so the file is not zeros to EOF.

    The outcome is the one a caller would want — someone who asserted `format=TAR` *and*
    asked for a provably complete listing gets told — but it is not what the review
    expected, so it is asserted here rather than left to be rediscovered.
    """
    from archivey import CorruptionError

    path = _archive("basic", "iso", tmp_path)
    data = path.read_bytes()
    first_nonzero = next(i for i, byte in enumerate(data) if byte)
    assert first_nonzero == 32768  # the system area ends; descriptors begin
    assert len(data) > first_nonzero  # ...and the file continues

    config = ArchiveyConfig(strict_archive_eof=True)
    with pytest.raises(CorruptionError):
        with open_archive(path, format=ArchiveFormat.TAR, config=config) as reader:
            reader.members()


def test_legitimately_empty_tar_stays_valid(tmp_path: Path) -> None:
    """F20 (guardrail): an empty TAR is legal, and any "zero members raises" rule breaks it.

    `tar cf empty.tar --files-from /dev/null` is a real thing and `tarfile` accepts it.
    Pinned so the cost of the strictest option in O8 stays visible.
    """
    import io
    import tarfile

    buf = io.BytesIO()
    tarfile.open(fileobj=buf, mode="w").close()
    path = tmp_path / "empty.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path, format=ArchiveFormat.TAR) as reader:
        assert reader.members() == []


# ---------------------------------------------------------------------------
# O2a — a solid out-of-order open() deliberately says nothing
# ---------------------------------------------------------------------------


def test_solid_out_of_order_open_emits_nothing(tmp_path: Path) -> None:
    """O2a (guardrail): the silence is the decision, not an omission.

    Three separate reviews have proposed adding a warning here. The answer is no, and
    `access-mode-and-cost` now says so: `cost.access_cost == SOLID` is on the receipt at
    open, before the caller does anything — a *better* signal than the rewind case gets,
    and that one only emits because it has no open-time signal at all. Emitting per
    `open()` would also produce one occurrence per member on exactly the workload that
    provokes it.

    Pinned so a fourth review that adds one breaks a test instead of a decision.
    """
    from archivey.cost import AccessCost

    path = _archive("basic", "tar.gz", tmp_path)
    with open_archive(path) as reader:
        assert reader.cost.access_cost is AccessCost.SOLID  # the signal, at open
        members = [m for m in reader.members() if m.type is _FILE]
        for member in reversed(members):  # worst case: fully out of order
            reader.read(member)
        assert dict(reader.diagnostics.counts) == {}
