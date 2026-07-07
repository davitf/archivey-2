"""Tests for the public ArchiveyConfig / ExtractionLimits surface (Phase 5 stage 1)."""

from __future__ import annotations

import dataclasses
import io
import logging
from unittest import mock

import pytest

import archivey
from archivey import (
    AcceleratorMode,
    ArchiveyConfig,
    DEFAULT_ARCHIVEY_CONFIG,
    ExtractionLimits,
    extract,
    open_archive,
)
from archivey.exceptions import ExtractionError, TruncatedError
from archivey.internal.config import stream_config_from_archivey
from archivey.types import ArchiveFormat


def test_config_types_are_frozen() -> None:
    cfg = ArchiveyConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.strict_archive_eof = True  # type: ignore[misc]
    limits = ExtractionLimits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_ratio = 1.0  # type: ignore[misc]


def test_default_config_is_module_constant() -> None:
    assert DEFAULT_ARCHIVEY_CONFIG is archivey.DEFAULT_ARCHIVEY_CONFIG
    assert DEFAULT_ARCHIVEY_CONFIG.use_rapidgzip is AcceleratorMode.AUTO
    assert DEFAULT_ARCHIVEY_CONFIG.strict_archive_eof is False
    assert DEFAULT_ARCHIVEY_CONFIG.extraction_limits == ExtractionLimits()


def test_open_archive_without_config_uses_defaults(tmp_path) -> None:
    (tmp_path / "f.txt").write_bytes(b"x")
    with open_archive(tmp_path) as ar:
        assert ar._config == DEFAULT_ARCHIVEY_CONFIG  # type: ignore[attr-defined]


def test_stream_config_derived_from_archivey_config() -> None:
    cfg = ArchiveyConfig(
        use_rapidgzip=AcceleratorMode.ON,
        use_indexed_bzip2=AcceleratorMode.OFF,
    )
    stream_cfg = stream_config_from_archivey(cfg, streaming=True)
    assert stream_cfg.streaming is True
    assert stream_cfg.use_rapidgzip is AcceleratorMode.ON
    assert stream_cfg.use_indexed_bzip2 is AcceleratorMode.OFF


def test_accelerator_modes_honored_via_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tarfile

    import archivey.internal.backends.tar_reader as tar_reader_module

    captured: list[object] = []

    def _capture_open(codec, source, *, config, stamp=None):
        captured.append(config)
        return mock.MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None)

    monkeypatch.setattr(tar_reader_module, "open_codec_stream", _capture_open)
    cfg = ArchiveyConfig(
        use_rapidgzip=AcceleratorMode.ON,
        use_indexed_bzip2=AcceleratorMode.OFF,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with open_archive(buf, format=ArchiveFormat.TAR_GZ, config=cfg) as ar:
        ar.members()
    assert captured
    assert captured[0].use_rapidgzip is AcceleratorMode.ON
    assert captured[0].use_indexed_bzip2 is AcceleratorMode.OFF


def test_strict_archive_eof_default_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.test_tar import _tar_missing_eof_block

    data = _tar_missing_eof_block()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert any("truncated" in r.getMessage().lower() for r in caplog.records)


def test_strict_archive_eof_true_raises() -> None:
    from tests.test_tar import _tar_missing_eof_block

    data = _tar_missing_eof_block()
    with pytest.raises(TruncatedError):
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(strict_archive_eof=True),
        ) as ar:
            ar.members()


def test_extract_limits_from_config(tmp_path) -> None:
    import zipfile

    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", b"x" * 5000)
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(
            src,
            dest,
            config=ArchiveyConfig(
                extraction_limits=ExtractionLimits(max_extracted_bytes=1000)
            ),
        )


def test_per_call_limits_override_config(tmp_path) -> None:
    import zipfile

    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", b"x" * 5000)
    dest = tmp_path / "out"
    tight = ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=100))
    with open_archive(src, config=tight) as reader:
        reader.extract_all(dest, limits=ExtractionLimits(max_extracted_bytes=10_000))
    assert (dest / "a.txt").exists()


def test_unlimited_preset_disables_guards(tmp_path) -> None:
    import zipfile

    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", b"x" * 5000)
    dest = tmp_path / "out"
    extract(src, dest, limits=ExtractionLimits.UNLIMITED)
    assert (dest / "a.txt").read_bytes() == b"x" * 5000


def test_public_api_exports_config_types() -> None:
    for name in (
        "ArchiveyConfig",
        "ExtractionLimits",
        "AcceleratorMode",
        "DEFAULT_ARCHIVEY_CONFIG",
    ):
        assert name in archivey.__all__
        assert hasattr(archivey, name)
