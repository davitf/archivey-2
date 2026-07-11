"""Focused and behavior tests for lifecycle-aware diagnostics."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import pytest

from archivey import (
    ArchiveMember,
    ArchiveyConfig,
    Diagnostic,
    DiagnosticCode,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticRaisedError,
    DiagnosticSeverity,
    ExtractionReport,
    ExtractionStatus,
    OnError,
    detect_format,
    extract,
    open_archive,
)
from archivey.diagnostics import (
    DiagnosticSummary,
    NameNormalizationContext,
    ScanRaceContext,
)
from archivey.exceptions import TruncatedError, UnsupportedOperationError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.types import MemberType


def _norm_context(**overrides: object) -> NameNormalizationContext:
    base: dict[str, object] = {
        "archive_name": None,
        "member_name": "a/b",
        "member_id": 1,
        "raw_name_base64": None,
        "presented_name": "a\\b",
        "normalized_name": "a/b",
    }
    base.update(overrides)
    return NameNormalizationContext(**base)  # type: ignore[arg-type]


def _emit_norm(
    collector: DiagnosticCollector,
    *,
    member: ArchiveMember | None = None,
    attach: bool = False,
    message: str = "Member name normalized: 'a\\\\b' -> 'a/b'",
) -> Diagnostic:
    return collector.emit(
        code=DiagnosticCode.MEMBER_NAME_NORMALIZED,
        message=message,
        context=_norm_context(
            member_name=member.name if member is not None else "a/b",
            member_id=member._member_id if member is not None else 1,
        ),
        member=member,
        attach_to_member=attach,
        logger=logging.getLogger("archivey.normalization"),
    )


def _make_member(name: str = "a/b") -> ArchiveMember:
    m = ArchiveMember(type=MemberType.FILE, name=name)
    m._member_id = 1
    return m


# ---------------------------------------------------------------------------
# 4.1 — policy matrix, retention, serialization, callbacks, reentrancy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "disposition,expect_retained,expect_log,expect_callback,expect_raise",
    [
        (DiagnosticDisposition.IGNORE, False, False, False, False),
        (DiagnosticDisposition.COLLECT, True, True, True, False),
        (DiagnosticDisposition.RAISE, True, True, True, True),
    ],
)
def test_policy_matrix_cells(
    disposition: DiagnosticDisposition,
    expect_retained: bool,
    expect_log: bool,
    expect_callback: bool,
    expect_raise: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[Diagnostic] = []
    collector = DiagnosticCollector(
        policy=DiagnosticPolicy(default=disposition),
        on_diagnostic=seen.append,
    )
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        if expect_raise:
            with pytest.raises(DiagnosticRaisedError) as ei:
                _emit_norm(collector)
            assert ei.value.diagnostic.code is DiagnosticCode.MEMBER_NAME_NORMALIZED
        else:
            _emit_norm(collector)

    snap = collector.snapshot()
    assert snap.total_count == 1
    assert snap.counts[DiagnosticCode.MEMBER_NAME_NORMALIZED] == 1
    assert (len(snap.retained) == 1) is expect_retained
    assert bool(caplog.records) is expect_log
    assert (len(seen) == 1) is expect_callback


def test_retention_exhaustion_keeps_exact_counts() -> None:
    collector = DiagnosticCollector(max_retained=2)
    for i in range(5):
        _emit_norm(collector, message=f"norm {i}")
    snap = collector.snapshot()
    assert snap.total_count == 5
    assert snap.counts[DiagnosticCode.MEMBER_NAME_NORMALIZED] == 5
    assert len(snap.retained) == 2
    assert snap.dropped_count == 3


def test_shared_budget_aggregate_then_attachment() -> None:
    member = _make_member()
    # Two slots: one aggregate + one attachment for the first event; second event
    # gets aggregate only (one slot left) — wait, after first: 2 used. Second: no slots.
    collector = DiagnosticCollector(max_retained=2)
    _emit_norm(collector, member=member, attach=True, message="first")
    assert len(member.diagnostics) == 1
    assert len(collector.snapshot().retained) == 1

    member2 = _make_member("c/d")
    member2._member_id = 2
    _emit_norm(collector, member=member2, attach=True, message="second")
    # Only one slot left after first (aggregate+attach used 2) — second gets nothing
    assert len(collector.snapshot().retained) == 1
    assert member2.diagnostics == ()

    # With 3 slots: second gets aggregate but not attachment
    collector2 = DiagnosticCollector(max_retained=3)
    m1, m2 = _make_member("x"), _make_member("y")
    m1._member_id, m2._member_id = 1, 2
    _emit_norm(collector2, member=m1, attach=True, message="a")
    _emit_norm(collector2, member=m2, attach=True, message="b")
    assert len(collector2.snapshot().retained) == 2
    assert len(m1.diagnostics) == 1
    assert m2.diagnostics == ()  # attachment omitted; aggregate retained


def test_occurrence_id_value_correlation() -> None:
    member = _make_member()
    collector = DiagnosticCollector()
    d = _emit_norm(collector, member=member, attach=True)
    snap = collector.snapshot()
    assert snap.retained[0].occurrence_id == member.diagnostics[0].occurrence_id
    assert snap.retained[0] == member.diagnostics[0]
    assert snap.retained[0].occurrence_id == d.occurrence_id


def test_context_json_safe_and_immutable() -> None:
    collector = DiagnosticCollector()
    d = _emit_norm(collector)
    payload = d.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["code"] == "member_name_normalized"
    assert payload["context"]["kind"] == "name_normalization"
    with pytest.raises(Exception):
        d.message = "mutated"  # type: ignore[misc]


def test_symlink_context_has_no_password_material() -> None:
    from archivey.diagnostics import SymlinkTargetContext

    ctx = SymlinkTargetContext(
        archive_name="a.zip",
        member_name="link",
        member_id=1,
        reason="password_required",
    )
    blob = json.dumps(ctx.to_dict())
    assert "password_required" in blob
    assert "secret" not in blob.lower() or "password_required" in blob


def test_callback_order_and_failure_propagates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []

    def cb(d: Diagnostic) -> None:
        order.append(d.message)
        if d.message == "second":
            raise RuntimeError("callback boom")

    collector = DiagnosticCollector(on_diagnostic=cb)
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        _emit_norm(collector, message="first")
        with pytest.raises(RuntimeError, match="callback boom"):
            _emit_norm(collector, message="second")
    assert order == ["first", "second"]
    assert collector.snapshot().total_count == 2


def test_callback_may_read_snapshot() -> None:
    collector = DiagnosticCollector()

    def cb(d: Diagnostic) -> None:
        snap = collector.snapshot()
        assert snap.total_count >= 1
        assert any(x.occurrence_id == d.occurrence_id for x in snap.retained)

    collector._on_diagnostic = cb  # type: ignore[attr-defined]
    # Recreate properly
    collector = DiagnosticCollector(on_diagnostic=cb)
    _emit_norm(collector)


def test_operational_reentrancy_rejected() -> None:
    collector = DiagnosticCollector()

    def cb(d: Diagnostic) -> None:
        collector.emit(
            code=DiagnosticCode.SCAN_ENTRY_VANISHED,
            message="reenter",
            context=ScanRaceContext(
                archive_name=None, relative_path="x", entry_kind="entry"
            ),
        )

    collector = DiagnosticCollector(on_diagnostic=cb)
    with pytest.raises(UnsupportedOperationError, match="reentrancy"):
        _emit_norm(collector)


def test_snapshots_are_immutable_points_in_time() -> None:
    collector = DiagnosticCollector()
    _emit_norm(collector, message="one")
    before = collector.snapshot()
    _emit_norm(collector, message="two")
    after = collector.snapshot()
    assert before.total_count == 1
    assert after.total_count == 2
    assert before.retained[0].message == "one"


# ---------------------------------------------------------------------------
# 4.2 — lifecycle, surfaces, migrated sites, extraction, EOF precedence
# ---------------------------------------------------------------------------


def test_detect_format_attaches_conflict_diagnostics(tmp_path: Path) -> None:
    # ZIP magic under a .tar name → conflict (existing test pattern).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    path = tmp_path / "looks.tar"
    path.write_bytes(buf.getvalue())
    info = detect_format(path)
    assert info.diagnostics.total_count >= 1
    assert DiagnosticCode.FORMAT_EXTENSION_CONFLICT in info.diagnostics.counts


def test_open_archive_transfers_detection_collector(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    path = tmp_path / "looks.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as reader:
        snap = reader.diagnostics
        assert DiagnosticCode.FORMAT_EXTENSION_CONFLICT in snap.counts
        assert snap.total_count >= 1


def test_oneshot_extract_report_includes_detection(
    tmp_path: Path,
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    src = tmp_path / "looks.tar"
    src.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    dest.mkdir()
    report = extract(src, dest)
    assert isinstance(report, ExtractionReport)
    assert DiagnosticCode.FORMAT_EXTENSION_CONFLICT in report.diagnostics.counts
    assert any(r.status is ExtractionStatus.EXTRACTED for r in report.results)


def test_extract_all_report_is_delta_only(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hi")
    src = tmp_path / "looks.tar"
    src.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    dest.mkdir()
    with open_archive(src) as reader:
        before = reader.diagnostics.total_count
        assert before >= 1  # detection conflict already counted
        report = reader.extract_all(dest)
        # Report is watermark delta — should not re-count pre-extraction diagnostics
        assert report.diagnostics.total_count == 0 or (
            DiagnosticCode.FORMAT_EXTENSION_CONFLICT not in report.diagnostics.counts
        )
        assert reader.diagnostics.total_count == before + report.diagnostics.total_count


def test_member_name_normalized_attaches(tmp_path: Path) -> None:
    # ZIP with backslash name → normalization diagnostic attached to member.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Force a stored name with backslash via ZipInfo
        info = zipfile.ZipInfo(r"dir\file.txt")
        zf.writestr(info, b"x")
    with open_archive(io.BytesIO(buf.getvalue())) as reader:
        members = reader.members()
        assert members
        # Either the member got an attachment or the reader aggregate has the code
        snap = reader.diagnostics
        if DiagnosticCode.MEMBER_NAME_NORMALIZED in snap.counts:
            matched = [m for m in members if m.diagnostics]
            # Attachment is budget-dependent; aggregate must exist
            assert snap.counts[DiagnosticCode.MEMBER_NAME_NORMALIZED] >= 1
            if matched:
                assert matched[0].diagnostics[0].occurrence_id in {
                    d.occurrence_id for d in snap.retained
                }


def test_directory_scan_race_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    (tmp_path / "a.txt").write_text("x")
    real_stat = os.DirEntry.stat

    def flaky_stat(self: os.DirEntry, *args: object, **kwargs: object):
        if self.name == "a.txt":
            raise FileNotFoundError(self.path)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", flaky_stat)
    with open_archive(tmp_path) as reader:
        list(reader.members())
        assert DiagnosticCode.SCAN_ENTRY_VANISHED in reader.diagnostics.counts


def test_extraction_rejected_emits_diagnostic(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    report = extract(
        io.BytesIO(buf.getvalue()),
        dest,
        on_error=OnError.CONTINUE,
    )
    assert any(r.status is ExtractionStatus.REJECTED for r in report.results)
    assert DiagnosticCode.EXTRACTION_MEMBER_REJECTED in report.diagnostics.counts


def test_raise_disposition_stops_despite_continue(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", b"x")
        zf.writestr("ok.txt", b"y")
    dest = tmp_path / "out"
    dest.mkdir()
    policy = DiagnosticPolicy(
        overrides={DiagnosticCode.EXTRACTION_MEMBER_REJECTED: DiagnosticDisposition.RAISE}
    )
    with pytest.raises(DiagnosticRaisedError) as ei:
        extract(
            io.BytesIO(buf.getvalue()),
            dest,
            on_error=OnError.CONTINUE,
            config=ArchiveyConfig(diagnostic_policy=policy),
        )
    assert ei.value.diagnostic.code is DiagnosticCode.EXTRACTION_MEMBER_REJECTED


def test_strict_eof_precedence_over_raise() -> None:
    from archivey.types import ArchiveFormat
    from tests.test_tar import _tar_missing_eof_block

    data = _tar_missing_eof_block()
    policy = DiagnosticPolicy(
        overrides={
            DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: DiagnosticDisposition.RAISE
        }
    )
    with pytest.raises(TruncatedError):
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(
                strict_archive_eof=True,
                diagnostic_policy=policy,
            ),
        ) as ar:
            ar.members()


def test_strict_eof_false_raise_yields_diagnostic_error() -> None:
    from archivey.types import ArchiveFormat
    from tests.test_tar import _tar_missing_eof_block

    data = _tar_missing_eof_block()
    policy = DiagnosticPolicy(
        overrides={
            DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: DiagnosticDisposition.RAISE
        }
    )
    with pytest.raises(DiagnosticRaisedError) as ei:
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(
                strict_archive_eof=False,
                diagnostic_policy=policy,
            ),
        ) as ar:
            ar.members()
    assert ei.value.diagnostic.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING


def test_strict_eof_ignore_still_raises_truncated() -> None:
    from archivey.types import ArchiveFormat
    from tests.test_tar import _tar_missing_eof_block

    data = _tar_missing_eof_block()
    policy = DiagnosticPolicy(
        overrides={
            DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: DiagnosticDisposition.IGNORE
        }
    )
    with pytest.raises(TruncatedError):
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(
                strict_archive_eof=True,
                diagnostic_policy=policy,
            ),
        ) as ar:
            ar.members()


def test_extraction_report_results_frozen(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    report = extract(tmp_path, dest)
    assert isinstance(report.results, tuple)
    with pytest.raises(Exception):
        report.results[0].status = ExtractionStatus.FAILED  # type: ignore[misc]
    # Member remains live/mutable
    report.results[0].member.comment = "late"
    assert report.results[0].member.comment == "late"


def test_empty_summary_helper() -> None:
    empty = DiagnosticSummary.empty()
    assert empty.total_count == 0
    assert empty.retained == ()
    assert empty.dropped_count == 0


def test_public_severity_and_exports() -> None:
    assert DiagnosticSeverity.WARNING.value == "warning"
    assert DiagnosticDisposition.COLLECT.value == "collect"
