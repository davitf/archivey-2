"""Multi-thread stress for promoted MemberStreams.CONCURRENT.

These tests also run under the Linux ``3.13t`` ``free-threaded-concurrency`` CI job
(marker ``concurrent_reader``). Optional backends (ISO/pycdlib) skip cleanly and are
not claimed covered by the core-only free-threaded job.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from archivey import MemberStreams, open_archive
from archivey.exceptions import ArchiveyUsageError

pytestmark = pytest.mark.concurrent_reader


def _dir_with_files(tmp_path: Path, n: int = 8) -> Path:
    root = tmp_path / "dir"
    root.mkdir()
    for i in range(n):
        (root / f"f{i}.txt").write_bytes(f"payload-{i}".encode() * 20)
    return root


def _make_zip(path: Path, n: int = 8) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n):
            zf.writestr(f"f{i}.txt", f"payload-{i}".encode() * 20)
    return path


def _make_tar(path: Path, *, compressed: bool = False, n: int = 8) -> Path:
    mode = "w:gz" if compressed else "w"
    with tarfile.open(path, mode) as tf:
        for i in range(n):
            data = f"payload-{i}".encode() * 20
            info = tarfile.TarInfo(name=f"f{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _expected(n: int = 8) -> dict[str, bytes]:
    return {f"f{i}.txt": f"payload-{i}".encode() * 20 for i in range(n)}


def _fan_out_read(path: Path, *, seekable: bool = False) -> dict[str, bytes]:
    flags = MemberStreams.CONCURRENT
    if seekable:
        flags |= MemberStreams.SEEKABLE
    with open_archive(path, member_streams=flags) as reader:
        members = [m for m in reader.members() if m.is_file]
        barrier = threading.Barrier(len(members))
        got: dict[str, bytes] = {}
        lock = threading.Lock()
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            try:
                barrier.wait(timeout=5)
                with reader.open(name) as stream:
                    data = stream.read()
                with lock:
                    got[name] = data
            except BaseException as exc:  # noqa: BLE001 - collect and re-raise below
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(m.name,)) for m in members]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()
        if errors:
            raise errors[0]
        return got


@pytest.mark.concurrent_reader
def test_multithread_directory_open_read(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    assert _fan_out_read(root) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_zip_open_close_refcnt_stress(tmp_path: Path) -> None:
    """Stress concurrent ZIP member open/close under free-threading.

    Stdlib ``zipfile`` updates ``_fileRefCnt`` without a lock on open/close; under
    CPython ``3.13t`` that races and asserts in ``_fpclose``. Repeat fan-out enough
    times to make the failure reliable before the ZIP handle lock fix.

    Also catches the native-codec path's concurrent CRC race: locked ``SlicingStream``
    views over ``ZipFile.fp`` must not call unlocked ``BufferedReader.tell`` at
    construction (see ``TestSlicingStreamLockedConstruction``).
    """
    path = _make_zip(tmp_path / "a.zip", n=16)
    expected = _expected(16)
    # Enough trials that an unlocked free-threaded run / unlocked-init tell race
    # fails consistently; a correct lock + construction keeps every trial green.
    for _ in range(80):
        assert _fan_out_read(path) == expected


@pytest.mark.concurrent_reader
def test_multithread_plain_tar_open_read(tmp_path: Path) -> None:
    path = _make_tar(tmp_path / "a.tar")
    assert _fan_out_read(path) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_gzip_tar_open_read(tmp_path: Path) -> None:
    path = _make_tar(tmp_path / "a.tar.gz", compressed=True)
    assert _fan_out_read(path) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_single_file_gz(tmp_path: Path) -> None:
    # Single-file archives have one member; stress overlapping open after materialization
    # is not applicable — instead fan reads across two concurrent opens of the same
    # archive path (two readers), and one CONCURRENT reader with SEEKABLE chunk reads.
    raw = b"hello-single-file-payload" * 50
    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress(raw))
    with open_archive(
        path, member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE
    ) as reader:
        reader.members()
        member = next(m for m in reader.members() if m.is_file)

        def read_all() -> bytes:
            with reader.open(member) as stream:
                return stream.read()

        with ThreadPoolExecutor(max_workers=2) as pool:
            # Two overlapping opens of the same member are allowed under CONCURRENT;
            # each must see the full payload.
            futs = [pool.submit(read_all), pool.submit(read_all)]
            results = [f.result(timeout=30) for f in as_completed(futs)]
        assert results == [raw, raw]


def test_multithread_iso_open_read(tmp_path: Path) -> None:
    pytest.importorskip("pycdlib")
    # Build a tiny ISO via pycdlib.
    import pycdlib

    iso_path = tmp_path / "a.iso"
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=1)
    for i in range(4):
        name = f"F{i}.TXT;1"
        data = f"payload-{i}".encode() * 20
        iso.add_fp(io.BytesIO(data), len(data), f"/{name}")
    iso.write(str(iso_path))
    iso.close()

    expected = {f"F{i}.TXT": f"payload-{i}".encode() * 20 for i in range(4)}
    # ISO member names may normalize; compare by payload set via open-by-member.
    with open_archive(iso_path, member_streams=MemberStreams.CONCURRENT) as reader:
        files = [m for m in reader.members() if m.is_file]
        assert len(files) >= 4
        barrier = threading.Barrier(len(files))
        got: dict[str, bytes] = {}
        lock = threading.Lock()

        def worker(member) -> None:
            barrier.wait(timeout=5)
            with reader.open(member) as stream:
                data = stream.read()
            with lock:
                got[member.name] = data

        threads = [threading.Thread(target=worker, args=(m,)) for m in files]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()
        assert set(got.values()) == set(expected.values())


@pytest.mark.concurrent_reader
def test_close_with_live_streams_defers_teardown(tmp_path: Path) -> None:
    """Live streams survive reader.close(); new opens fail; bytes remain readable."""
    path = _make_zip(tmp_path / "a.zip", n=4)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    members = [m for m in reader.members() if m.is_file]
    streams = [reader.open(m.name) for m in members[:2]]
    reader.close()
    with pytest.raises(ArchiveyUsageError, match="closed"):
        reader.open(members[2].name)
    assert {s.read() for s in streams} == {
        _expected(4)[members[0].name],
        _expected(4)[members[1].name],
    }
    for s in streams:
        s.close()


@pytest.mark.concurrent_reader
def test_multithread_open_rejected_during_stream_members(tmp_path: Path) -> None:
    path = _make_zip(tmp_path / "a.zip", n=4)
    with open_archive(path, member_streams=MemberStreams.CONCURRENT) as reader:
        it = reader.stream_members()
        next(it)
        errors: list[BaseException] = []

        def try_open() -> None:
            try:
                reader.open("f0.txt")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=try_open) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors and all(isinstance(e, ArchiveyUsageError) for e in errors)
        list(it)


# --- Coordinated first-touch materialization + draining close ----------------------------


@pytest.mark.concurrent_reader
def test_concurrent_first_touch_open_materializes_once(tmp_path: Path) -> None:
    """N threads first-touch open() share one materialization and read correct bytes."""
    path = _make_zip(tmp_path / "a.zip", n=8)
    expected = _expected(8)
    names = list(expected)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    scan_calls = {"n": 0}
    original_iter = reader._iter_members

    def counting_iter():
        scan_calls["n"] += 1
        return original_iter()

    reader._iter_members = counting_iter  # type: ignore[method-assign]
    barrier = threading.Barrier(len(names))
    got: dict[str, bytes] = {}
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            barrier.wait(timeout=5)
            with reader.open(name) as stream:
                data = stream.read()
            with lock:
                got[name] = data
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()
    reader.close()
    if errors:
        raise errors[0]
    assert got == expected
    assert scan_calls["n"] == 1


@pytest.mark.concurrent_reader
def test_concurrent_first_touch_materialization_failure_wakes_waiters(
    tmp_path: Path,
) -> None:
    """Failed first-touch leaves no partial snapshot; waiters see the error / clean retry."""
    path = _make_zip(tmp_path / "a.zip", n=4)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    original_iter = reader._iter_members
    fail_once = {"armed": True}
    barrier = threading.Barrier(4)
    results: list[BaseException | list] = []
    lock = threading.Lock()

    def flaky_iter():
        if fail_once["armed"]:
            fail_once["armed"] = False
            raise OSError("simulated corrupt header")
        return original_iter()

    reader._iter_members = flaky_iter  # type: ignore[method-assign]

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            members = reader.members()
            with lock:
                results.append(members)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    # Cache must never have been published as a partial list during the failure.
    # After the flaky first attempt, a waiter (or the same electing path on retry)
    # may succeed — collect successes and failures.
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures, "at least the electing thread should observe the translated error"
    assert all(not isinstance(f, ArchiveyUsageError) for f in failures)
    # No partial publication: either empty successes (all saw the error before retry
    # completed) or full member lists only.
    for members in successes:
        assert len(members) == 4
        assert {m.name for m in members if m.is_file} == set(_expected(4))
    assert reader._members_cache is None or len(reader._members_cache) == 4
    reader.close()


@pytest.mark.concurrent_reader
def test_close_drains_in_flight_workers_then_closes(tmp_path: Path) -> None:
    """close() waits for in-flight open() workers; escaped streams stay readable."""
    path = _make_zip(tmp_path / "a.zip", n=4)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    reader.members()  # publish so open() work is post-materialization
    entered = threading.Event()
    release = threading.Event()
    original_open_member = reader._open_member

    def blocking_open_member(member):  # noqa: ANN001
        entered.set()
        assert release.wait(timeout=5)
        return original_open_member(member)

    reader._open_member = blocking_open_member  # type: ignore[method-assign]
    errors: list[BaseException] = []
    stream_box: dict[str, object] = {}

    def worker() -> None:
        try:
            stream_box["s"] = reader.open("f0.txt")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(timeout=5)
    close_done = threading.Event()

    def closer() -> None:
        reader.close()
        close_done.set()

    ct = threading.Thread(target=closer)
    ct.start()
    # close must block while the worker is still inside open().
    assert not close_done.wait(timeout=0.2)
    release.set()
    t.join(timeout=10)
    ct.join(timeout=10)
    assert close_done.is_set()
    assert not errors
    stream = stream_box["s"]
    assert stream is not None
    assert stream.read() == _expected(4)["f0.txt"]  # type: ignore[union-attr]
    stream.close()  # type: ignore[union-attr]
    with pytest.raises(ArchiveyUsageError, match="closed"):
        reader.open("f1.txt")


@pytest.mark.concurrent_reader
def test_concurrent_double_close_is_idempotent(tmp_path: Path) -> None:
    path = _make_zip(tmp_path / "a.zip", n=2)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    reader.members()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def closer() -> None:
        try:
            barrier.wait(timeout=5)
            reader.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=closer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert not errors
    # Second close after both finish remains a no-op.
    reader.close()


@pytest.mark.concurrent_reader
def test_concurrent_double_close_exception_group_on_dual_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simultaneous inner-close + teardown failure surfaces once as ExceptionGroup."""
    path = _make_zip(tmp_path / "a.zip", n=1)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    stream = reader.open("f0.txt")

    def boom_close_archive() -> None:
        raise OSError("teardown failed")

    monkeypatch.setattr(reader, "_close_archive", boom_close_archive)

    def boom_inner_close() -> None:
        raise OSError("stream close failed")

    # Force the stream's inner close to fail when the lease drops into teardown via
    # stream.close() after reader.close() — exercise the ExceptionGroup path on the
    # stream side (reader close itself only teardowns when no leases remain).
    reader.close()
    # Patch after reader close: closing the escaped stream triggers deferred teardown.
    inner = stream._ensure_open()
    monkeypatch.setattr(inner, "close", boom_inner_close)
    with pytest.raises(ExceptionGroup) as exc_info:
        stream.close()
    assert "member-stream close and archive teardown both failed" in str(exc_info.value)


# --- Internal-open exemption is thread-scoped (deep N3) ---------------------------------


def test_internal_open_exemption_is_thread_scoped() -> None:
    """extract_all's internal-open window admits the owning thread's opens as children
    of its root pass; a FOREIGN thread's open must still be rejected (pre-fix it was
    silently admitted, and unlocked shared handles then served wrong bytes)."""
    from archivey.internal.reader_state import ReaderState

    state = ReaderState(member_streams=MemberStreams(0), open_site=None)
    root = state.acquire_pass("extract_all")
    state.begin_internal_opens()
    try:
        # Owning thread: admitted as a child of the root.
        token = state.acquire_worker("open")
        assert token.parent is root
        state.release_worker(token)

        # Foreign thread: rejected like during any active root pass.
        outcome: list[object] = []

        def foreign() -> None:
            try:
                outcome.append(state.acquire_worker("open"))
            except ArchiveyUsageError as exc:
                outcome.append(exc)

        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert len(outcome) == 1 and isinstance(outcome[0], ArchiveyUsageError)
    finally:
        state.end_internal_opens()
        state.release_pass(root)


@pytest.mark.concurrent_reader
def test_foreign_thread_open_rejected_during_extract_all(tmp_path: Path) -> None:
    """End-to-end N3: while extract_all runs on thread A (no CONCURRENT declared), a
    second thread's reader.open() must raise instead of being silently admitted under
    extract_all's internal-open exemption."""
    archive = _make_tar(tmp_path / "a.tar")
    dest = tmp_path / "out"
    with open_archive(archive) as reader:
        in_extract = threading.Event()
        proceed = threading.Event()
        outcome: list[str] = []

        def on_progress(progress: object) -> None:
            if not in_extract.is_set():
                in_extract.set()
                # Hold the extraction mid-pass until the foreign open has resolved.
                assert proceed.wait(timeout=30)

        def foreign_open() -> None:
            try:
                assert in_extract.wait(timeout=30)
                try:
                    with reader.open("f0.txt") as stream:
                        stream.read()
                    outcome.append("admitted")
                except ArchiveyUsageError:
                    outcome.append("rejected")
            finally:
                proceed.set()

        thread = threading.Thread(target=foreign_open)
        thread.start()
        reader.extract_all(dest, on_progress=on_progress)
        thread.join(timeout=30)
        assert not thread.is_alive()
        assert outcome == ["rejected"]
