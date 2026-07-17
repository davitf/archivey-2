"""Public configuration types for archivey."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from archivey.diagnostics import DiagnosticPolicy, OnDiagnostic

if TYPE_CHECKING:
    from archivey.types import ArchiveMember


class AcceleratorMode(Enum):
    """Tri-state control for an optional random-access accelerator backend.

    - ``ON``  — always use the accelerator (raise ``PackageNotInstalledError`` if its
      package is absent: the caller asked for it explicitly).
    - ``OFF`` — never use it; the stream stays sequential-only.
    - ``AUTO`` — use it only when seekability was declared
      (``MemberStreams.SEEKABLE`` / seek demand). Without declared seek demand, AUTO
      leaves the cheaper sequential backend in place (no index/accelerator work). When
      AUTO would enable the accelerator but its package is absent, fall back to
      sequential silently (it is an enhancement, not a requirement). For the
      ``rapidgzip`` DEFLATE-family path, AUTO also requires the known compressed
      input size to reach :data:`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (see
      :meth:`enabled_for`) **and** a verifiable decompressed size
      (``StreamConfig.expected_decompressed_size``, or gzip ISIZE) so truncation
      cannot be silently short-read.
    """

    AUTO = "auto"
    ON = "on"
    OFF = "off"

    def enabled_for(
        self,
        *,
        seekable: bool,
        available: bool,
        input_size: int | None = None,
        min_size: int | None = None,
    ) -> bool:
        """Resolve the tri-state to "use the accelerator?".

        ``ON`` always returns ``True`` (the caller checks availability and raises
        ``PackageNotInstalledError`` if the package is missing — the user asked for it
        explicitly; ``min_size`` is ignored). ``AUTO`` enables it only when
        seekability is declared and the package is available, so a missing package
        falls back silently. When ``min_size`` is set and ``input_size`` is known and
        strictly below that threshold, ``AUTO`` also falls back (tiny members do not
        repay per-stream accelerator setup). Unknown ``input_size`` keeps the
        pre-threshold AUTO behaviour.
        """
        if self is AcceleratorMode.OFF:
            return False
        if self is AcceleratorMode.ON:
            return True
        # AUTO: only pay for seek machinery when the caller asked for seekable streams.
        if not (available and seekable):
            return False
        if min_size is not None and input_size is not None and input_size < min_size:
            return False
        return True


# Minimum known compressed input size (bytes) before ``use_rapidgzip`` AUTO selects
# rapidgzip for a DEFLATE-family stream (gzip / zlib / raw deflate). Below this,
# stdlib backends stay cheaper: rapidgzip's per-stream index/thread setup dominates
# for tiny members (many-small ZIP/gzip case). Benchmarked in
# ``scripts/bench_rapidgzip_auto_threshold.py``; see the rapidgzip-deflate-zlib
# acceleration design note. ``ON`` ignores this; unknown size keeps pre-threshold AUTO.
RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE: int = 1 * 1024 * 1024


@dataclass(frozen=True)
class ExtractionLimits:
    """Decompression-bomb limits for :func:`archivey.extract` / :meth:`extract_all`.

    ``None`` on a guard field disables that guard. :attr:`UNLIMITED` disables all four.
    """

    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar[ExtractionLimits]


ExtractionLimits.UNLIMITED = ExtractionLimits(
    max_extracted_bytes=None,
    max_ratio=None,
    max_entries=None,
)


@dataclass(frozen=True)
class ListingLimits:
    """Caps for materializing a member list (``members`` / ``scan_members`` / extract prep).

    Applied from the reader's open :attr:`ArchiveyConfig.listing_limits` for its lifetime.
    ``None`` on a field disables that guard. :attr:`UNLIMITED` disables both.
    ``stream_members`` / forward-only iteration do not enforce these caps.
    """

    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20  # 64 MiB

    UNLIMITED: ClassVar[ListingLimits]


ListingLimits.UNLIMITED = ListingLimits(
    max_members=None,
    max_metadata_bytes=None,
)


@dataclass(frozen=True)
class ArchiveyConfig:
    """Library tuning knobs passed explicitly as ``config=`` to :func:`open_archive` / :func:`extract`.

    Per-call operationals (``format``, ``streaming``, ``password``, extraction's
    ``members``/``filter``/``policy``/…) stay keyword arguments — not part of this object.
    """

    # Tri-state for the [seekable] rapidgzip accelerator (gzip / zlib / raw deflate).
    # Under AUTO, also requires known compressed input ≥ RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    # and a verifiable decompressed size (so truncation cannot be silently swallowed).
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    # Tri-state for rapidgzip's bundled bzip2 random-access backend.
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    # Legacy encoding for a ZIP member name stored without the UTF-8 flag whose bytes are
    # also not valid UTF-8 (the sniff prefers UTF-8 first). Default cp437 per APPNOTE; set a
    # local codepage (e.g. "cp1252", "shift_jis") for a known-legacy corpus. An explicit
    # ``encoding=`` on ``open_archive`` overrides this and disables the sniff entirely.
    zip_unflagged_fallback_encoding: str = "cp437"
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    diagnostic_policy: DiagnosticPolicy = field(default_factory=DiagnosticPolicy)
    max_retained_diagnostic_references: int = 256
    on_diagnostic: OnDiagnostic | None = None


DEFAULT_ARCHIVEY_CONFIG = ArchiveyConfig()


@dataclass(frozen=True)
class PasswordRequest:
    """Context passed to a :data:`PasswordProvider` when a password is needed."""

    member: ArchiveMember | None
    """The member being decrypted, or ``None`` for archive-level (header) decryption."""

    attempt: int
    """1 on the first ask for this unit; increments after a wrong-password retry."""


PasswordProvider = Callable[[PasswordRequest], str | bytes | None]
"""Callable consulted when static password candidates fail for an encrypted unit."""

PasswordInput = str | bytes | Sequence[str | bytes] | PasswordProvider | None
